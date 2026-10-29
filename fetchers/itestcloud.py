"""iTEST training fetcher using the site's current-training pages."""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from fetchers._browser import launch_context
from shared import credentials
from shared.models import ScheduleEventCreate

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

NAME = "itestcloud"
BASE = "https://itestcloud.unipus.cn"
LOGIN_URL = (
    "https://sso.unipus.cn/sso/login?service="
    "https%3A%2F%2Fitestcloud.unipus.cn%2Futest%2Fitest%2Flogin"
)
TRAINING_PAGES = {
    "class": "/utest/itest/s/train",
    "mock": "/utest/itest/s/jcxl/mock",
    "basic": "/utest/itest/s/jcxl/jcxl",
}
_CATEGORY_LABELS = {
    "class": "班级训练",
    "mock": "模考训练",
    "basic": "基础训练",
}
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LOG = logging.getLogger(__name__)


def _text(node) -> str:
    return " ".join(node.stripped_strings) if node else ""


def _parse_local_datetime(raw: str, *, end_of_day: bool = False) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            value = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                value = value.replace(hour=23, minute=59, second=59) if end_of_day else value
            return value.replace(tzinfo=_SHANGHAI).astimezone(UTC)
        except ValueError:
            continue
    return None


def _parse_time_range(raw: str) -> tuple[datetime | None, datetime | None]:
    parts = (raw or "").split("@", maxsplit=1)
    if len(parts) != 2:
        parts = re.split(r"\s*[~～]\s*", raw or "", maxsplit=1)
    if len(parts) != 2:
        return None, None
    return _parse_local_datetime(parts[0]), _parse_local_datetime(parts[1], end_of_day=True)


def _map_completion_status(completion_raw: str) -> str:
    completion = (completion_raw or "").strip().lower()
    if any(marker in completion for marker in ("未达标", "未完成", "not achieved", "not completed", "failed")):
        return "open"
    if any(marker in completion for marker in ("已达标", "已完成")) or re.search(
        r"\b(achieved|completed|passed)\b", completion
    ):
        return "submitted"
    return "open"


def parse_training_list(html: str, category: str) -> list[ScheduleEventCreate]:
    """Parse one current-training category page without browser dependencies."""
    if category not in TRAINING_PAGES:
        raise ValueError(f"Unknown iTEST training category: {category}")

    soup = BeautifulSoup(html, "html.parser")
    page_url = urljoin(BASE, TRAINING_PAGES[category])
    events: list[ScheduleEventCreate] = []
    for card in soup.select(".train_task_item"):
        identity = card.select_one(".now-banji1.head[data-id]")
        task_id = identity.get("data-id", "").strip() if identity else ""
        title = _text(card.select_one(".taskName"))
        if not task_id or not title:
            continue

        action = card.select_one(".task-link")
        range_raw = action.get("data-time", "").strip() if action else ""
        start_at, end_at = _parse_time_range(range_raw)

        details: list[dict[str, str]] = []
        for row in card.select("dl"):
            label = _text(row.select_one("dt"))
            value = _text(row.select_one("dd"))
            if label or value:
                details.append({"label": label, "value": value})

        score_label = card.select_one(".task-score-dabiao")
        score_raw = _text(score_label)
        completion_raw = _text(score_label.find_next_sibling("dd")) if score_label else ""
        status = _map_completion_status(completion_raw)
        raw_parts = re.split(r"\s*(?:@|[~～])\s*", range_raw, maxsplit=1)
        description = "\n".join(
            f"{item['label']}: {item['value']}" if item["label"] else item["value"]
            for item in details
            if item["label"] or item["value"]
        )

        events.append(
            ScheduleEventCreate(
                platform=NAME,
                source_id=f"{category}:{task_id}",
                title=title,
                description=description,
                meeting_url=page_url,
                start_at=start_at,
                end_at=end_at,
                tz_original="Asia/Shanghai",
                status=status,
                raw_payload={
                    "task_id": task_id,
                    "category": category,
                    "category_label": _CATEGORY_LABELS[category],
                    "start_raw": raw_parts[0].strip() if len(raw_parts) == 2 else "",
                    "end_raw": raw_parts[1].strip() if len(raw_parts) == 2 else "",
                    "time_range_raw": range_raw,
                    "score_raw": score_raw,
                    "completion_raw": completion_raw,
                    "details": details,
                    "source_url": page_url,
                },
            )
        )
    return events


async def _is_logged_in(page: Page) -> bool:
    try:
        response = await page.goto(
            urljoin(BASE, TRAINING_PAGES["class"]),
            wait_until="domcontentloaded",
            timeout=30000,
        )
        return (
            response is not None
            and response.ok
            and urlsplit(page.url).hostname == urlsplit(BASE).hostname
            and await page.locator(".stu-train-data-wrap").count() > 0
        )
    except Exception:  # noqa: BLE001
        return False


async def _perform_login(page: Page, username: str, password: str) -> None:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    checkboxes = page.locator('input[type="checkbox"]')
    if await checkboxes.count() >= 2:
        agreement = checkboxes.nth(await checkboxes.count() - 1)
        if not await agreement.is_checked():
            await agreement.click()

    username_field = page.locator(
        'input[type="text"]:visible, input[type="tel"]:visible'
    ).first
    password_field = page.locator('input[type="password"]:visible').first
    if not await username_field.count() or not await password_field.count():
        raise RuntimeError("iTEST login form fields were not found")

    await username_field.fill(username)
    await password_field.fill(password)
    await page.locator("#login").click()
    try:
        await page.wait_for_url(
            lambda url: urlsplit(url).hostname == urlsplit(BASE).hostname,
            timeout=60000,
        )
    except Exception as exc:  # noqa: BLE001
        body = (await page.locator("body").inner_text()).lower()
        if any(marker in body for marker in ("captcha", "geetest", "滑动验证", "安全验证")):
            raise RuntimeError(
                "iTEST requires an interactive CAPTCHA; login was not completed"
            ) from exc
        raise RuntimeError("iTEST login failed; check the saved itestcloud credentials") from exc


class ItestcloudFetcher:
    name = NAME

    def __init__(self, headless: bool = True):
        self.headless = headless

    async def fetch(self, since: datetime | None = None) -> list[ScheduleEventCreate]:
        from playwright.async_api import async_playwright

        cred = credentials.load(NAME)
        if not cred or not cred.get("username") or not cred.get("password"):
            raise RuntimeError("Save iTEST credentials with `concourse creds set itestcloud` first")

        async with async_playwright() as pw:
            context: BrowserContext = await launch_context(pw, NAME, headless=self.headless)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                if not await _is_logged_in(page):
                    await _perform_login(page, cred["username"], cred["password"])

                events: list[ScheduleEventCreate] = []
                failures: list[str] = []
                for category, route in TRAINING_PAGES.items():
                    try:
                        response = await page.goto(
                            urljoin(BASE, route),
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                        if response is None or not response.ok:
                            status = response.status if response else "no response"
                            raise RuntimeError(f"HTTP {status}")
                        if urlsplit(page.url).hostname != urlsplit(BASE).hostname:
                            raise RuntimeError("session redirected to login")
                        events.extend(parse_training_list(await page.content(), category))
                    except Exception as exc:  # noqa: BLE001
                        failures.append(category)
                        _LOG.warning("iTEST %s training page failed: %s", category, exc)

                if failures and not events and len(failures) == len(TRAINING_PAGES):
                    raise RuntimeError("All iTEST training category pages failed")
                return events
            finally:
                await context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch current iTEST training tasks")
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(ItestcloudFetcher(headless=not args.no_headless).fetch())
    import json

    payload = [event.model_dump(mode="json") for event in result]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
