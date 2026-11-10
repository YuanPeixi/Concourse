import asyncio

import pytest

from backend import login, scheduler
from shared.errors import LoginRequired, NetworkFailure, MESSAGES


@pytest.fixture()
def isolated_scheduler(monkeypatch):
    records = []
    async def record(name, **fields):
        records.append(fields)
    monkeypatch.setattr(scheduler, "_record", record)
    monkeypatch.setattr(scheduler, "_load_fetcher", lambda _: object())
    monkeypatch.setattr(scheduler, "_locks", {})
    monkeypatch.setattr(scheduler, "_active", set())
    monkeypatch.setattr(scheduler, "_background", set())
    monkeypatch.setattr(scheduler, "_pending", set())
    monkeypatch.setattr(scheduler, "_stopping", False)
    monkeypatch.setattr(scheduler, "RETRY_DELAY", 0)
    return records


@pytest.mark.asyncio
async def test_network_retries_bounded_and_error_text_never_stored(monkeypatch, isolated_scheduler):
    attempts = 0
    async def fail(_):
        nonlocal attempts
        attempts += 1
        raise NetworkFailure("https://private.example/?token=never-store-this")
    monkeypatch.setattr(scheduler, "_fetch", fail)
    await scheduler.run_fetcher("codeforces", "manual")
    assert attempts == 2
    assert isolated_scheduler[-1]["last_error"] == MESSAGES["network"]
    assert "never-store-this" not in repr(isolated_scheduler)


@pytest.mark.asyncio
async def test_invalid_login_is_not_retried(monkeypatch, isolated_scheduler):
    attempts = 0
    async def fail(_):
        nonlocal attempts
        attempts += 1
        raise LoginRequired("sensitive username password")
    monkeypatch.setattr(scheduler, "_fetch", fail)
    await scheduler.run_fetcher("scholat", "manual")
    assert attempts == 1
    assert isolated_scheduler[-1]["last_error"] == MESSAGES["login"]


@pytest.mark.asyncio
async def test_timeout_releases_platform_and_finalizes_fetch(monkeypatch, isolated_scheduler):
    closed = asyncio.Event()
    async def stuck(_):
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    monkeypatch.setattr(scheduler, "_fetch", stuck)
    monkeypatch.setattr(scheduler, "FETCH_TIMEOUT", .02)
    await scheduler.run_fetcher("codeforces", "manual")
    assert closed.is_set() and not scheduler.is_running("codeforces")
    assert isolated_scheduler[-1]["last_error"] == MESSAGES["network"]


@pytest.mark.asyncio
async def test_repeated_click_and_shutdown_cleanup(monkeypatch, isolated_scheduler):
    started, closed = asyncio.Event(), asyncio.Event()
    async def stuck(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    monkeypatch.setattr(scheduler, "_fetch", stuck)
    assert scheduler.trigger_now("codeforces")
    assert not scheduler.trigger_now("codeforces")
    await started.wait()
    assert not login.start("codeforces")
    await scheduler.stop_scheduler()
    assert closed.is_set() and not scheduler._background and not scheduler._pending


@pytest.mark.asyncio
async def test_login_cancel_before_browser_starts_releases_reservation(isolated_scheduler):
    assert login.start("scholat")
    assert not scheduler.trigger_now("scholat")
    task = login._sessions["scholat"]["task"]
    assert login.control("scholat", "cancel")
    await asyncio.gather(task, return_exceptions=True)
    assert not login.waiting("scholat") and "scholat" not in scheduler._pending
