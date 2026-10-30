"""Codeforces fetcher —— 纯 HTTP 模板。

contest.list 端点公开，无需登录。这是纯 HTTP fetcher 的参考实现：
没有 Playwright、没有 DPAPI、没有浏览器数据目录。

config 示例：
  codeforces:
    notify_registered: true   # 提醒已报名的
    notify_unregistered: true # 提醒未报名的
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from shared.models import ScheduleEventCreate, SubmissionPolicy

NAME = "codeforces"
API_BASE = "https://codeforces.com/api"


class CodeforcesFetcher:
    name = NAME

    def __init__(self, notify_registered: bool = True, notify_unregistered: bool = True):
        self.notify_registered = notify_registered
        self.notify_unregistered = notify_unregistered

    async def fetch(self, since: datetime | None = None) -> list[ScheduleEventCreate]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{API_BASE}/contest.list")
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "OK":
                return []

            now = datetime.now(timezone.utc)
            events: list[ScheduleEventCreate] = []
            for c in data["result"]:
                # phase: BEFORE / CODING / SYSTEM_TEST / FINISHED (uppercase)
                phase = c.get("phase", "")
                if phase != "BEFORE":
                    continue

                start_ts = c.get("startTimeSeconds")
                duration_s = c.get("durationSeconds", 0)
                if start_ts is None:
                    continue
                start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
                end = start + timedelta(seconds=duration_s)

                # 只看未来 30 天内的比赛，避免噪音
                if start - now > timedelta(days=30):
                    continue

                contest_id = str(c["id"])
                registered = c.get("participantType") is not None

                if registered and not self.notify_registered:
                    continue
                if not registered and not self.notify_unregistered:
                    continue

                events.append(
                    ScheduleEventCreate(
                        platform=NAME,
                        source_id=f"contest:{contest_id}",
                        title=c.get("name", f"Codeforces Contest {contest_id}"),
                        description=c.get("description", "") or "",
                        start_at=start,
                        end_at=end,
                        tz_original="UTC",
                        status="open",
                        submission_policy=SubmissionPolicy.NONE,
                        meeting_url=f"https://codeforces.com/contest/{contest_id}",
                        raw_payload={
                            # 定时事项：前端按 start_at 显示/计算紧急度（见 frontend/src/eventTime.ts）
                            "kind": "contest",
                            "type": c.get("type"),
                            "phase": phase,
                            "registered": registered,
                            "duration_seconds": duration_s,
                        },
                    )
                )
            return events
