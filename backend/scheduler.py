"""定时调度 fetcher 并把结果 upsert 进数据库。

用 APScheduler 进程内调度；同一平台加锁防并发重抓。
启用/停用与 cron 覆盖存在 fetcher_states 表，优先于 config.yaml；
修改后通过 apply_settings() 热更新，无需重启。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.repository import get_fetcher_state, list_fetcher_states, upsert_event
from shared import config as cfg
from shared.models import FetcherState
from shared.errors import MESSAGES, classify, stored_kind

log = logging.getLogger("concourse.scheduler")

_scheduler: AsyncIOScheduler | None = None
_locks: dict[str, asyncio.Lock] = {}
_background: set[asyncio.Task] = set()
_active: set[asyncio.Task] = set()
_pending: set[str] = set()
_stopping = False
FETCH_TIMEOUT = 180
RETRY_DELAY = 2


def _load_fetcher(name: str) -> Any:
    from shared.platforms import REGISTRY
    return REGISTRY[name].create()


def available_fetchers() -> list[str]:
    from shared.platforms import REGISTRY
    return sorted(REGISTRY)


def default_enabled(name: str) -> bool:
    # 只有写进 config.yaml schedule 的才默认启用；新加的 fetcher 文件默认停用，需在界面里打开
    return name in (cfg.load_config().get("schedule") or {})


def effective(name: str, state: FetcherState | None) -> tuple[bool, str]:
    enabled = state.enabled if state is not None else default_enabled(name)
    cron = (state.cron if state is not None and state.cron else None) or cfg.schedule_for(name)
    return enabled, cron


def validate_cron(expr: str) -> None:
    CronTrigger.from_crontab(expr)


def is_running(name: str) -> bool:
    lock = _locks.get(name)
    return lock is not None and lock.locked()


def next_run_at(name: str) -> datetime | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(f"fetcher.{name}")
    return job.next_run_time if job else None


async def _fetch(fetcher: Any) -> list:
    # uvicorn --reload on Windows forces SelectorEventLoop, which cannot spawn
    # subprocesses (Playwright needs to). Run fetch() on a Proactor loop in a worker thread.
    if sys.platform == "win32" and not isinstance(
        asyncio.get_running_loop(), asyncio.ProactorEventLoop
    ):
        def _run() -> list:
            loop = asyncio.ProactorEventLoop()
            try:
                return loop.run_until_complete(fetcher.fetch())
            finally:
                loop.close()

        return await asyncio.to_thread(_run)
    return await fetcher.fetch()


async def _record(name: str, **fields: Any) -> None:
    from backend.db import SessionLocal

    async with SessionLocal() as session:
        state = await get_fetcher_state(session, name)
        if state is None:
            state = FetcherState(name=name, enabled=default_enabled(name))
        for k, v in fields.items():
            setattr(state, k, v)
        session.add(state)
        await session.commit()


async def run_fetcher(name: str, trigger: str = "schedule") -> bool:
    if _stopping:
        return False
    task = asyncio.current_task()
    _active.add(task)
    try:
        return await _run_fetcher(name, trigger)
    finally:
        _active.discard(task)
        _pending.discard(name)


async def _run_fetcher(name: str, trigger: str = "schedule") -> bool:
    """运行一次 fetcher 并记录结果；已在运行则跳过并返回 False。"""
    if trigger != "manual":
        from backend.db import SessionLocal
        async with SessionLocal() as session:
            previous = await get_fetcher_state(session, name)
        if previous and stored_kind(previous.last_error) in {"login", "verification"}:
            return False
    lock = _locks.setdefault(name, asyncio.Lock())
    if lock.locked():
        log.info("fetcher %s 已在运行，跳过本次触发", name)
        return False
    async with lock:
        from backend.db import SessionLocal

        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        await _record(name, last_trigger=trigger, last_started_at=started)
        log.info("fetcher %s 开始 (%s)", name, trigger)

        count = changed = 0
        try:
            fetcher = _load_fetcher(name)
            async with asyncio.timeout(FETCH_TIMEOUT):
                for attempt in range(2):
                    try:
                        events = await _fetch(fetcher)
                        break
                    except Exception as error:
                        if classify(error) != "network" or attempt == 1:
                            raise
                        await asyncio.sleep(RETRY_DELAY)
            count = len(events)
            async with SessionLocal() as session:
                for ev in events:
                    _, is_changed = await upsert_event(session, ev)
                    changed += int(is_changed)
        except Exception as exc:  # noqa: BLE001
            kind = classify(exc)
            log.warning("fetcher %s failed (%s)", name, kind)
            await _record(
                name,
                last_finished_at=datetime.now(timezone.utc),
                last_status="error",
                last_error=MESSAGES[kind],
                last_count=count,
                last_changed=changed,
                last_duration_ms=int((time.monotonic() - t0) * 1000),
            )
            return True

        finished = datetime.now(timezone.utc)
        await _record(
            name,
            last_finished_at=finished,
            last_success_at=finished,
            last_status="ok",
            last_error="",
            last_count=count,
            last_changed=changed,
            last_duration_ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("fetcher %s 完成: 共 %d 条, 其中 %d 条更新", name, count, changed)
        return True


def trigger_now(name: str) -> bool:
    """后台立即运行；已在运行返回 False。"""
    if _stopping or name in _pending or is_running(name):
        return False
    _pending.add(name)
    task = asyncio.create_task(run_fetcher(name, trigger="manual"))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return True


def _apply_one(name: str, enabled: bool, cron: str) -> None:
    assert _scheduler is not None
    job_id = f"fetcher.{name}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
    if not enabled:
        return
    try:
        trigger = CronTrigger.from_crontab(cron)
    except Exception as exc:  # noqa: BLE001
        log.warning("无法解析 cron %r (%s): %s", cron, name, exc)
        return
    _scheduler.add_job(
        run_fetcher, trigger, args=[name], id=job_id,
        max_instances=1, coalesce=True, misfire_grace_time=300,
    )


async def apply_settings(name: str | None = None) -> None:
    """按 DB 中的设置（缺省回落 config.yaml）重建一个或全部 job。"""
    if _scheduler is None:
        return
    from backend.db import SessionLocal

    async with SessionLocal() as session:
        states = {s.name: s for s in await list_fetcher_states(session)}
    names = [name] if name else available_fetchers()
    for n in names:
        _apply_one(n, *effective(n, states.get(n)))


async def start_scheduler() -> AsyncIOScheduler:
    global _scheduler, _stopping
    _stopping = False
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.start()
    await apply_settings()
    return _scheduler


async def stop_scheduler() -> None:
    global _scheduler, _stopping
    _stopping = True
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    tasks = list(_background | _active)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background.clear()
    _active.clear()
    _pending.clear()
    _locks.clear()
