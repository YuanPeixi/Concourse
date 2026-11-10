"""Explicit user-controlled browser login under the same platform lock as scraping."""
from __future__ import annotations
import asyncio

from backend import scheduler
from shared import config
from shared.errors import MESSAGES, classify

_sessions: dict[str, dict] = {}
LOGIN_TIMEOUT = 180


def waiting(name):
    return name in _sessions


def start(name):
    if scheduler._stopping or name in scheduler._pending or scheduler.is_running(name):
        return False
    scheduler._pending.add(name)
    state = {"event": asyncio.Event(), "action": None}
    _sessions[name] = state
    task = asyncio.create_task(_run(name, state))
    scheduler._background.add(task)
    state["task"] = task
    def finished(task):
        scheduler._background.discard(task)
        scheduler._pending.discard(name)
        _sessions.pop(name, None)
    task.add_done_callback(finished)
    return True


def control(name, action):
    if name not in _sessions:
        return False
    if action == "cancel":
        _sessions[name]["task"].cancel()
    _sessions[name]["action"] = action
    _sessions[name]["event"].set()
    return True


async def _run(name, state):
    from playwright.async_api import async_playwright
    from fetchers._browser import launch_context

    lock = scheduler._locks.setdefault(name, asyncio.Lock())
    try:
        async with lock, asyncio.timeout(LOGIN_TIMEOUT), async_playwright() as pw:
            context = await launch_context(pw, name, headless=False)
            try:
                context.on("close", lambda *args: state["event"].set())
                page = context.pages[0] if context.pages else await context.new_page()
                if name == "scholat":
                    from fetchers.scholat import LOGIN_URL
                    url = LOGIN_URL
                else:
                    from fetchers.itestcloud import HOME_URL
                    url = HOME_URL
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await state["event"].wait()
                if state["action"] == "complete":
                    if name == "scholat":
                        from fetchers.scholat import _is_logged_in
                        ids = config.fetcher_kwargs(name).get("course_ids") or []
                        valid = bool(ids) and await _is_logged_in(context, ids[0])
                    else:
                        from fetchers.itestcloud import _load_training_page, TRAINING_SOURCES
                        valid = await _load_training_page(page, TRAINING_SOURCES[0][1]) is not None
                    if not valid:
                        from shared.errors import LoginRequired
                        raise LoginRequired()
                    await scheduler._record(name, last_error="", last_status=None)
            finally:
                await context.close()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await scheduler._record(name, last_status="error", last_error=MESSAGES[classify(error)])
    finally:
        _sessions.pop(name, None)
        scheduler._pending.discard(name)
