"""iTEST（itestcloud.unipus.cn）fetcher —— Playwright 登录 + HTML/接口解析。

登录流程：
1. 先直接打开班级训练页；持久化 context（``.browser-data/itestcloud``）仍有效时跳过登录。
2. 否则打开首页，点击 ``.sign-in``（会在新标签页打开 SSO；已登录则直接到 exam）。
3. 在 SSO 页勾选协议、填入已保存的凭据并提交；遇到验证码时明确报错。

数据来源：
- 训练：服务端渲染的 HTML，三类——班级训练 ``/s/train``（含 ``?historyCls=true`` 已结束）、
  模考训练 ``/s/jcxl/mock``、基础训练 ``/s/jcxl/jcxl``。
- 考试：``POST /utest/itest-mobile-api/student/exam/list`` 返回 JSON
  （``history=false`` 待完成 / ``history=true`` 已完成）。
parse_training_list / parse_exam_list 是纯函数，可离线测试。

任一页面或接口失败都会抛错，由 scheduler 记为 error，不会把部分结果当成成功。

凭据：``concourse creds set itestcloud`` → {"username": ..., "password": ...}

config 示例：
  itestcloud:
    headless: true
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
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
HOME_URL = f"{BASE}/"
EXAM_URL = f"{BASE}/utest/itest/s/exam"
EXAM_API = f"{BASE}/utest/itest-mobile-api/student/exam/list"
SSO_URL = re.compile(r"https://sso\.unipus\.cn/sso/login(?:\?.*)?$")
EXAM_URL_PATTERN = re.compile(r"https://itestcloud\.unipus\.cn/utest/itest/s/exam(?:[/?#].*)?$")

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
# (category, route)：按顺序抓取；同一任务在当前/历史页都出现时以后者为准
TRAINING_SOURCES = (
    ("class", TRAINING_PAGES["class"]),
    ("class", f"{TRAINING_PAGES['class']}?historyCls=true"),
    ("mock", TRAINING_PAGES["mock"]),
    ("basic", TRAINING_PAGES["basic"]),
)
EXAM_COURSE = "考试"

EXAM_PAGE_SIZE = 20
MAX_EXAM_PAGES = 20

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LOG = logging.getLogger(__name__)

# 完成标记：先判未达标（"not achieved" 也包含 "achieved"）
_NOT_PASSED_MARKERS = ("未达标", "未完成", "not achieved", "not completed", "failed")
_PASSED_MARKERS = ("已达标", "已完成")
_PASSED_RE = re.compile(r"\b(achieved|completed|passed)\b")
_CAPTCHA_MARKERS = ("captcha", "geetest", "滑动验证", "安全验证")


# ---------------------------------------------------------------------------
# 解析（纯函数，可离线测试）
# ---------------------------------------------------------------------------
def _text(node) -> str:
    return " ".join(node.stripped_strings) if node else ""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _ms_to_utc(ms: int | float | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _parse_local_datetime(raw: str, *, end_of_day: bool = False) -> datetime | None:
    """解析上海时间字符串为 UTC；仅日期时 end_of_day 补到 23:59:59。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            value = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            value = value.replace(hour=23, minute=59, second=59)
        return value.replace(tzinfo=_SHANGHAI).astimezone(UTC)
    _LOG.warning("iTEST 无法识别的时间格式：%r", raw)
    return None


def _split_time_range(raw: str) -> tuple[str, str]:
    """``start@end`` 或 ``start ~ end`` → (start_raw, end_raw)；格式不符返回空串。"""
    parts = re.split(r"\s*(?:@|[~～])\s*", (raw or "").strip(), maxsplit=1)
    if len(parts) != 2:
        return "", ""
    return parts[0].strip(), parts[1].strip()


def map_training_status(completion_raw: str, end: datetime | None, now: datetime | None = None) -> str:
    """已达标 → submitted；未达标/未作答且已截止 → closed；否则 open；无法识别 → unknown。"""
    completion = completion_raw.strip().lower()
    if completion and not any(marker in completion for marker in _NOT_PASSED_MARKERS):
        if any(marker in completion for marker in _PASSED_MARKERS) or _PASSED_RE.search(completion):
            return "submitted"
        return "unknown"
    # 未达标或尚未作答
    if end is not None and end < _now(now):
        return "closed"
    return "open"


def parse_training_list(
    html: str, category: str, now: datetime | None = None
) -> list[ScheduleEventCreate]:
    """解析一个训练分类页（当前或历史），不依赖浏览器。"""
    if category not in TRAINING_PAGES:
        raise ValueError(f"Unknown iTEST training category: {category}")

    soup = BeautifulSoup(html, "html.parser")
    page_url = urljoin(BASE, TRAINING_PAGES[category])
    events: list[ScheduleEventCreate] = []
    for card in soup.select(".train_task_item"):
        identity = card.select_one(".head[data-id]")
        task_id = identity.get("data-id", "").strip() if identity else ""
        title = _text(card.select_one(".taskName"))
        if not task_id or not title:
            continue

        action = card.select_one(".task-link")
        range_raw = action.get("data-time", "").strip() if action else ""
        start_raw, end_raw = _split_time_range(range_raw)
        start_at = _parse_local_datetime(start_raw)
        end_at = _parse_local_datetime(end_raw, end_of_day=True)

        # 侧栏（成绩 + 是否达标）单独处理，不混入 details
        details: list[dict[str, str]] = []
        for row in card.select("dl"):
            if row.find_parent(class_="task-sidebar"):
                continue
            label = _text(row.select_one("dt"))
            value = _text(row.select_one("dd"))
            if label or value:
                details.append({"label": label, "value": value})

        score_raw = _text(card.select_one(".task-sidebar dt"))
        completion_raw = _text(card.select_one(".task-sidebar dd"))
        status = map_training_status(completion_raw, end_at, now)

        desc_lines = [f"{d['label']} {d['value']}".strip() for d in details]
        if score_raw:
            desc_lines.append(f"成绩 {score_raw}" + (f"（{completion_raw}）" if completion_raw else ""))

        events.append(
            ScheduleEventCreate(
                platform=NAME,
                source_id=f"{category}:{task_id}",
                course=_CATEGORY_LABELS[category],
                title=title,
                description="\n".join(desc_lines),
                meeting_url=page_url,
                start_at=start_at,
                end_at=end_at,
                tz_original="Asia/Shanghai",
                status=status,
                raw_payload={
                    "kind": "train",
                    "task_id": task_id,
                    "category": category,
                    "start_raw": start_raw,
                    "end_raw": end_raw,
                    "time_range_raw": range_raw,
                    "action_raw": _text(action),
                    "score_raw": score_raw,
                    "completion_raw": completion_raw,
                    "details": details,
                },
            )
        )
    return events


def map_exam_status(item: dict[str, Any], now: datetime | None = None) -> str:
    """已交卷 → submitted；未交且考试窗口已过 → closed（缺考）；其余 open。"""
    if item.get("ansSubmitTime") or item.get("finish") == 2:
        return "submitted"
    end = _ms_to_utc(item.get("restrictEndTime"))
    if end is not None and end < _now(now):
        return "closed"
    return "open"


def parse_exam_list(data: list[dict[str, Any]], now: datetime | None = None) -> list[ScheduleEventCreate]:
    """data = 接口 ``rs.data`` 数组。"""
    events: list[ScheduleEventCreate] = []
    for item in data:
        ks_id = item.get("ksId")
        if ks_id is None:
            continue
        judge = item.get("canViewKaojuanJudgeBean") or {}
        score_text = judge.get("scoreText") or ""
        desc_parts = [f"总分 {item['totalScore']}"] if item.get("totalScore") else []
        if score_text:
            desc_parts.append(f"成绩 {score_text}")
        elif judge.get("kaojuanReason") == "缺考":
            desc_parts.append("缺考")
        events.append(
            ScheduleEventCreate(
                platform=NAME,
                source_id=f"exam:{ks_id}",
                course=EXAM_COURSE,
                title=item.get("ksName") or f"考试 {ks_id}",
                description="；".join(desc_parts),
                meeting_url=EXAM_URL,
                start_at=_ms_to_utc(item.get("restrictBeginTime")),
                end_at=_ms_to_utc(item.get("restrictEndTime")),
                tz_original="Asia/Shanghai",
                status=map_exam_status(item, now),
                raw_payload={
                    "kind": "exam",
                    "ks_id": ks_id,
                    "ksd_id": item.get("ksdId"),
                    "cls_id": item.get("clsId"),
                    "finish": item.get("finish"),
                    "being_or_over": item.get("beingOrOver"),
                    "exam_tag": item.get("examTag"),
                    "total_score": item.get("totalScore"),
                    "score_text": score_text,
                    "begin_raw": item.get("restrictBeginTimeStr"),
                    "end_raw": item.get("restrictEndTimeStr"),
                    "submit_raw": item.get("ansSubmitTimeStr"),
                    "absent_reason": judge.get("kaojuanReason"),
                },
            )
        )
    return events


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
async def _load_training_page(page: Page, route: str) -> str | None:
    """打开训练页并返回 HTML；被重定向（未登录）返回 None，HTTP 错误抛出。"""
    url = urljoin(BASE, route)
    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    if response is None or not response.ok:
        status = response.status if response else "no response"
        raise RuntimeError(f"iTEST 页面 {route} 请求失败：HTTP {status}")
    landed = urlsplit(page.url)
    if landed.hostname != urlsplit(BASE).hostname or landed.path.rstrip("/") != urlsplit(url).path:
        return None
    return await page.content()


async def _open_login_or_exam(context: BrowserContext, page: Page) -> Page:
    """点击首页登录入口，返回落在 exam 或 SSO 的页面（SSO 会在新标签页打开）。"""
    await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    sign_in = page.locator(".sign-in").first
    await sign_in.wait_for(state="attached", timeout=30000)
    await sign_in.click(force=True)

    know_clicked = False
    for _ in range(60):
        for p in reversed(context.pages):
            if EXAM_URL_PATTERN.match(p.url) or SSO_URL.match(p.url):
                return p
        # 兜底：没有新标签页时，点公告弹窗的"知道了"
        know = page.locator(".btn-know:visible").first
        if not know_clicked and await know.count():
            await know.click(force=True)
            know_clicked = True
        await page.wait_for_timeout(500)
    raise RuntimeError(
        "iTEST 未跳转到 SSO 或 exam，当前页面：" + ", ".join(p.url for p in context.pages)
    )


async def _perform_login(context: BrowserContext, page: Page, username: str, password: str) -> Page:
    page = await _open_login_or_exam(context, page)
    if EXAM_URL_PATTERN.match(page.url):
        return page

    username_input = page.get_by_role("textbox", name="手机号/邮箱/用户名")
    password_input = page.get_by_role("textbox", name="密码")
    agreement = page.get_by_role("checkbox", name=re.compile("我已阅读并同意"))

    await username_input.wait_for(state="visible", timeout=30000)
    await password_input.wait_for(state="visible", timeout=30000)
    # 按名称定位协议勾选框，不依赖页面上 checkbox 的数量
    if await agreement.count() and not await agreement.first.is_checked(timeout=5000):
        await agreement.first.check(timeout=5000)
    await username_input.fill(username)
    await password_input.fill(password)

    await page.get_by_role("button", name="登录").click()
    try:
        await page.wait_for_url(EXAM_URL_PATTERN, timeout=60000)
    except Exception as exc:
        body = (await page.locator("body").inner_text()).lower()
        if any(marker in body for marker in _CAPTCHA_MARKERS):
            raise RuntimeError("iTEST 登录需要交互式验证码，未完成登录") from exc
        raise RuntimeError(f"iTEST 登录未进入 exam 页面，当前 URL：{page.url}") from exc
    return page


async def login_with_saved_credentials(context: BrowserContext) -> Page:
    """用已保存的凭据登录持久化 context，返回已登录的页面。"""
    cred = credentials.load(NAME)
    if not cred:
        raise RuntimeError(f"未找到 iTEST 凭据，请先运行 `uv run concourse creds set {NAME}`。")
    username = cred.get("username")
    password = cred.get("password")
    if not isinstance(username, str) or not username:
        raise RuntimeError("iTEST 凭据缺少 username。")
    if not isinstance(password, str) or not password:
        raise RuntimeError("iTEST 凭据缺少 password。")

    page = context.pages[0] if context.pages else await context.new_page()
    return await _perform_login(context, page, username, password)


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
class ItestcloudFetcher:
    name = NAME

    def __init__(self, headless: bool = True):
        self.headless = headless

    async def fetch(self, since: datetime | None = None) -> list[ScheduleEventCreate]:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            context = await launch_context(pw, NAME, headless=self.headless)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                first_route = TRAINING_SOURCES[0][1]
                try:
                    first_html = await _load_training_page(page, first_route)
                except Exception as exc:  # noqa: BLE001 — 会话检查失败一律视为需要登录
                    _LOG.info("iTEST 会话检查失败，改为登录：%s", exc)
                    first_html = None
                if first_html is None:
                    page = await login_with_saved_credentials(context)

                # 以 source_id 去重：同一任务可能同时出现在当前页和历史页
                by_id: dict[str, ScheduleEventCreate] = {}
                for index, (category, route) in enumerate(TRAINING_SOURCES):
                    # 会话检查时已加载过第一页，直接复用
                    html = (first_html if index == 0 else None) or await _load_training_page(page, route)
                    if html is None:
                        raise RuntimeError(f"iTEST 训练页 {route} 被重定向，会话可能已失效")
                    for event in parse_training_list(html, category):
                        by_id[event.source_id] = event

                for event in await self._fetch_exams(page):
                    by_id[event.source_id] = event
                return list(by_id.values())
            finally:
                await context.close()

    async def _fetch_exams(self, page: Page) -> list[ScheduleEventCreate]:
        items: list[dict[str, Any]] = []
        for history in (False, True):
            for cur_page in range(1, MAX_EXAM_PAGES + 1):
                resp = await page.request.post(
                    EXAM_API,
                    data={
                        "curPage": cur_page,
                        "pageSize": EXAM_PAGE_SIZE,
                        "history": history,
                        "examTag": 0,
                    },
                    timeout=30000,
                )
                if not resp.ok:
                    raise RuntimeError(f"iTEST 考试列表请求失败：HTTP {resp.status}")
                body = await resp.json()
                if body.get("code") != 0:
                    raise RuntimeError(f"iTEST 考试列表返回错误：{body.get('msg')}")
                rs = body.get("rs") or {}
                data = rs.get("data") or []
                items += data
                total_pages = rs.get("totalPage") or 0
                if not data or cur_page >= total_pages:
                    break
            else:
                _LOG.warning("iTEST 考试列表超过 %d 页，后续页未抓取 (history=%s)", MAX_EXAM_PAGES, history)
        return parse_exam_list(items)


if __name__ == "__main__":
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(description="Fetch iTEST trainings and exams")
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(ItestcloudFetcher(headless=not args.no_headless).fetch())
    print(json.dumps([e.model_dump(mode="json") for e in result], ensure_ascii=False, indent=2))
