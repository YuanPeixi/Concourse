"""Scholat fetcher —— Playwright + BeautifulSoup。

这是 Playwright 系 fetcher 的参考实现。
parse_homework_list / parse_homework_detail 从原 scholat_homework.py 移植，
纯函数无副作用，可直接用录制快照做单测。

凭据：shared.credentials.load("scholat") → {"username": ..., "password": ...}
浏览器数据：持久化 context 保存登录态，路径 .browser-data/scholat/

config 示例：
  scholat:
    course_ids: ["3017", "3042"]
    include_submitted: true   # false 时只返回未提交的事项
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from fetchers._browser import launch_context
from shared import credentials
from shared.errors import LoginRequired

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext
from shared.models import ScheduleEventCreate, SubmissionPolicy

NAME = "scholat"
BASE = "https://www.scholat.com"
LOGIN_URL = f"{BASE}/login.html?source=pc"


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------
@dataclass
class Homework:
    homework_id: str
    title: str
    status: str
    url: str
    publisher: str = ""
    deadline: str = ""
    content: str = ""
    attachments: list[dict] = field(default_factory=list)
    policy_raw: str = ""         # 截止日期格中的徽章，如 "不可延时提交"
    submitted_link: bool = False  # 操作栏是否有 "已交" 链接


UNSUBMITTED_STATUS = "未提交"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _normalize(s: str) -> str:
    return re.sub(r"[\s 　]+", "", s or "")


def _normalize_keep(s: str) -> str:
    s = (s or "").replace(" ", " ").replace("　", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _html_to_text(node: Tag | None) -> str:
    if node is None:
        return ""
    for br in node.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for block in node.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4"]):
        block.append(NavigableString("\n"))
    for img in node.find_all("img"):
        src = img.get("src") or ""
        img.replace_with(NavigableString(f"[图片: {urljoin(BASE, src)}]" if src else "[图片]"))
    text = node.get_text().replace("\r\n", "\n").replace("\r", "\n")
    lines = [_normalize_keep(ln) for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "" and (not out or out[-1] == ""):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def _parse_deadline_to_utc(s: str) -> datetime | None:
    if not s:
        return None
    CST = timezone(timedelta(hours=8))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(s.strip(), fmt)
            return naive.replace(tzinfo=CST).astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        # 只有日期时按当天结束处理
        naive = datetime.strptime(s.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        return naive.replace(tzinfo=CST).astimezone(timezone.utc)
    except ValueError:
        return None


# 学者网列表页状态徽章（页面说明）：未提交 / 按时提交 / 延时提交 / 已终止提交
_SUBMITTED_STATUSES = ("按时提交", "延时提交", "已提交", "已批改", "已查看")


def _map_status(raw: str, submitted_link: bool = False) -> str:
    n = _normalize(raw)
    if "未提交" in n:
        return "open"
    if "终止" in n:
        # 老师关闭了提交：交过算 submitted，没交也已无法操作
        return "submitted" if submitted_link else "closed"
    if n in _SUBMITTED_STATUSES or submitted_link:
        return "submitted"
    return "unknown"


def _infer_policy(policy_raw: str) -> SubmissionPolicy:
    n = _normalize(policy_raw)
    if "不可延时" in n or "不可补交" in n:
        return SubmissionPolicy.NONE
    if "补交一次" in n:
        return SubmissionPolicy.ONCE
    if "延时" in n or "补交" in n:
        return SubmissionPolicy.LATE
    return SubmissionPolicy.NONE


# ---------------------------------------------------------------------------
# 页面解析（纯函数，可离线测试）
# ---------------------------------------------------------------------------
def parse_homework_list(html: str, course_id: str) -> list[Homework]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="datalist") or soup.find("table", class_="datalist")
    if table is None:
        return []
    result: list[Homework] = []
    for row in table.find_all("tr"):
        link = row.find("a", href=re.compile(r"S_oneHomework\.html", re.I))
        if link is None:
            continue
        href = link.get("href", "")
        m = re.search(r"homeworkId=(\d+)", href, re.I)
        if not m:
            continue
        homework_id = m.group(1)
        status_cell = row.find("td", id=f"upload{homework_id}") or row.find("td")
        status = _normalize_keep(status_cell.get_text(" ", strip=True)) if status_cell else ""
        title = _normalize_keep(link.get("title") or link.get_text(" ", strip=True))
        publisher, deadline, policy_raw = "", "", ""
        for cell in row.find_all("td"):
            txt = _normalize_keep(cell.get_text(" ", strip=True))
            dm = re.match(r"\d{4}-\d{2}-\d{2}(\s+\d{2}:\d{2}(:\d{2})?)?", txt)
            if dm and not deadline:
                # 截止日期格可能带提交规则徽章："2026-11-05 不可延时提交"
                deadline = dm.group(0)
                policy_raw = txt[dm.end():].strip()
            elif publisher == "" and cell.find("a", href=re.compile(r"^\.\./|^/")) and "作业要求" not in txt:
                publisher = txt
        submitted_link = any(
            _normalize(a.get_text()) == "已交" for a in row.find_all("a")
        )
        result.append(
            Homework(
                homework_id=homework_id,
                title=title,
                status=status,
                url=urljoin(BASE + "/course/", href),
                publisher=publisher,
                deadline=deadline,
                policy_raw=policy_raw,
                submitted_link=submitted_link,
            )
        )
    return result


def parse_homework_detail(html: str, hw: Homework) -> Homework:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find(class_="notice_title")
    if title_el:
        hw.title = _normalize_keep(title_el.get_text(" ", strip=True)) or hw.title
    meta_el = soup.find(class_="notice_d")
    if meta_el:
        meta = _normalize_keep(meta_el.get_text(" ", strip=True))
        m = re.search(r"发布人[：:]\s*([^\s]+)", meta)
        if m:
            hw.publisher = m.group(1)
        m = re.search(r"截止时间[：:]\s*([0-9\-: ]+)", meta)
        if m:
            hw.deadline = m.group(1).strip()
    hw.content = _html_to_text(soup.find(class_="notice_content"))
    seen: set[str] = set()
    for a in soup.find_all("a", href=re.compile(r"S_downloadHomeworkLink\.html", re.I)):
        href = a.get("href", "")
        m = re.search(r"homeworkLinkId=(\d+)", href, re.I)
        container = a.find_parent("div") or a
        name_span = container.find("span", title=True) or container.find("span")
        disp = _normalize_keep(name_span.get_text(" ", strip=True)) if name_span else ""
        size = ""
        sm = re.search(r"\(([^)]+)\)", disp)
        if sm:
            size = sm.group(1)
            disp = disp[: sm.start()].strip()
        name = (name_span.get("title") if name_span and name_span.get("title") else disp) or ""
        url = urljoin(BASE + "/course/", href)
        if url not in seen:
            seen.add(url)
            hw.attachments.append({
                "name": name,
                "size": size,
                "url": url,
                "link_id": m.group(1) if m else None,
            })
    return hw


# ---------------------------------------------------------------------------
# 浏览器 / 登录
# ---------------------------------------------------------------------------
async def _is_logged_in(context: BrowserContext, probe_course_id: str) -> bool:
    try:
        resp = await context.request.get(
            f"{BASE}/course/S_homeworkList.html?courseId={probe_course_id}",
            timeout=15000,
        )
        return resp.ok and 'id="studentId"' in await resp.text()
    except Exception:  # noqa: BLE001
        return False


async def _perform_login(context: BrowserContext, username: str, password: str) -> bool:
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    try:
        if not await page.locator("#loginForm").count():
            await page.wait_for_selector("#loginHref", state="visible", timeout=20000)
            await page.click("#loginHref")
            await page.wait_for_selector("#loginForm", state="visible", timeout=30000)
    except Exception:  # noqa: BLE001
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_selector("#loginForm", state="visible", timeout=30000)

    await page.wait_for_selector("#j_username", state="visible", timeout=30000)
    await page.fill("#j_username", username)
    await page.fill("#j_password_ext", password)
    try:
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
            await page.click("#login")
    except Exception:  # noqa: BLE001
        await page.click("#login")
        await page.wait_for_load_state("domcontentloaded", timeout=60000)

    await page.wait_for_timeout(1500)
    for _ in range(10):
        try:
            if not await page.locator("#loginHref").count():
                return True
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(1000)
    return False


async def _ensure_logged_in(context: BrowserContext, cred: dict | None, probe_course_id: str) -> None:
    if await _is_logged_in(context, probe_course_id):
        return
    if cred and cred.get("username") and cred.get("password"):
        ok = await _perform_login(context, cred["username"], cred["password"])
        if ok:
            return
    raise LoginRequired(
        "Scholat 登录失败：请先运行 `uv run python -m fetchers.scholat --save-credentials` 保存凭据，"
        "或检查用户名/密码是否正确。"
    )


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
class ScholatFetcher:
    name = NAME

    def __init__(
        self,
        course_ids: list[str] | None = None,
        include_submitted: bool = True,
        headless: bool = True,
    ):
        self.course_ids = course_ids or []
        self.include_submitted = include_submitted
        self.headless = headless

    async def fetch(self, since: datetime | None = None) -> list[ScheduleEventCreate]:
        from playwright.async_api import async_playwright
        if not self.course_ids:
            raise ValueError("请先配置学者网课程 ID")
        cred = credentials.load(NAME)
        async with async_playwright() as pw:
            context = await launch_context(pw, NAME, headless=self.headless)
            try:
                probe_id = self.course_ids[0]
                await _ensure_logged_in(context, cred, probe_id)
                events: list[ScheduleEventCreate] = []
                for course_id in self.course_ids:
                    resp = await context.request.get(
                        f"{BASE}/course/S_homeworkList.html?courseId={course_id}",
                        timeout=30000,
                    )
                    if not resp.ok:
                        continue
                    homeworks = parse_homework_list(await resp.text(), course_id)
                    for hw in homeworks:
                        if not self.include_submitted and _map_status(hw.status, hw.submitted_link) != "open":
                            continue
                        detail_resp = await context.request.get(hw.url, timeout=30000)
                        if detail_resp.ok:
                            hw = parse_homework_detail(await detail_resp.text(), hw)
                        events.append(_to_event(hw, course_id))
                return events
            finally:
                await context.close()


def _to_event(hw: Homework, course_id: str) -> ScheduleEventCreate:
    return ScheduleEventCreate(
        platform=NAME,
        source_id=f"course:{course_id}:hw:{hw.homework_id}",
        course=course_id,
        title=hw.title,
        description=hw.content,
        end_at=_parse_deadline_to_utc(hw.deadline),
        tz_original="Asia/Shanghai",
        status=_map_status(hw.status, hw.submitted_link),
        submission_policy=_infer_policy(hw.policy_raw),
        organizer_name=hw.publisher or None,
        meeting_url=hw.url,
        raw_payload={
            "publisher": hw.publisher,
            "deadline_raw": hw.deadline,
            "status_raw": hw.status,
            "policy_raw": hw.policy_raw,
            "submitted_link": hw.submitted_link,
            "attachments": hw.attachments,
            "course_id": course_id,
        },
    )


# ---------------------------------------------------------------------------
# CLI（凭据管理 + 单独运行）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import asyncio
    import getpass
    import json as _json

    parser = argparse.ArgumentParser(description="Scholat fetcher")
    parser.add_argument("--save-credentials", action="store_true")
    parser.add_argument("--forget-credentials", action="store_true")
    parser.add_argument("--course-ids", nargs="+", default=["3017", "3042"])
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()

    if args.forget_credentials:
        print("已删除" if credentials.forget(NAME) else "未找到凭据")
    elif args.save_credentials:
        u = input("学者网用户名/邮箱: ").strip()
        p = getpass.getpass("密码（不回显）: ")
        path = credentials.save(NAME, {"username": u, "password": p})
        print(f"已加密保存：{path}")
    else:
        fetcher = ScholatFetcher(
            course_ids=args.course_ids,
            headless=not args.no_headless,
        )
        results = asyncio.run(fetcher.fetch())
        print(_json.dumps(
            [e.model_dump(mode="json") for e in results],
            ensure_ascii=False,
            indent=2,
        ))
