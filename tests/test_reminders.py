from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.reminders import pending, acknowledge, update_settings, target_for
from shared.models import Event, FetcherState

NOW = datetime(2026, 11, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(FetcherState(name="codeforces", enabled=True))
        await session.commit()
        yield session
    await engine.dispose()


def event(**changes):
    values = dict(platform="codeforces", source_id="one", title="Test event", content_hash="x",
                  start_at=NOW + timedelta(hours=2), end_at=NOW + timedelta(hours=4),
                  raw_payload={"kind": "contest"})
    values.update(changes)
    return Event(**values)


async def setup(session):
    value = event()
    session.add(value)
    await session.commit()
    await update_settings(session, True, [60], NOW)
    return value


@pytest.mark.asyncio
async def test_first_baseline_dedup_failure_and_ack(session):
    await setup(session)
    assert await pending(session, NOW) == []
    notices = await pending(session, NOW + timedelta(hours=1))
    assert len(notices) == 1
    assert await pending(session, NOW + timedelta(hours=1)) == notices  # no ack = retry
    await acknowledge(session, notices[0]["delivery_ids"], NOW + timedelta(hours=1))
    assert await pending(session, NOW + timedelta(hours=1)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["done", "deleted", "disabled", "target"])
async def test_delivery_rechecks_changes(session, change):
    value = await setup(session)
    assert len(await pending(session, NOW + timedelta(hours=1))) == 1
    if change == "done": value.status = "done"
    if change == "deleted": value.deleted_at = NOW
    if change == "target": value.start_at = NOW + timedelta(days=3)
    if change == "disabled":
        state = await session.get(FetcherState, "codeforces")
        state.enabled = False
        session.add(state)
    session.add(value)
    await session.commit()
    assert await pending(session, NOW + timedelta(hours=1)) == []


@pytest.mark.asyncio
async def test_settings_reset_baseline_and_catchup_window(session):
    await setup(session)
    await update_settings(session, False, [60], NOW)
    assert await pending(session, NOW + timedelta(hours=1)) == []
    await update_settings(session, True, [60], NOW + timedelta(hours=3))
    assert await pending(session, NOW + timedelta(hours=3)) == []


@pytest.mark.asyncio
async def test_same_event_missed_leads_are_merged(session):
    value = event(start_at=NOW + timedelta(hours=25))
    session.add(value)
    await session.commit()
    await update_settings(session, True, [1440, 60], NOW)
    notices = await pending(session, NOW + timedelta(hours=24))
    assert len(notices) == 1 and len(notices[0]["delivery_ids"]) == 2
    assert await pending(session, NOW + timedelta(hours=49)) == []


def test_target_semantics_all_day_and_unknown_submission():
    assert target_for(event()).hour == 2
    assert target_for(event(raw_payload={"kind": "exam"})).hour == 2
    assert target_for(event(raw_payload={"kind": "homework"})).hour == 4
    assert target_for(event(start_at=None, end_at=None)) is None
    value = event(platform="moodle", all_day=True, tz_original="Asia/Shanghai",
                  raw_payload={"kind": "deadline"}, end_at=NOW + timedelta(hours=15))
    assert target_for(value).hour == 1  # 09:00 Asia/Shanghai
    assert value.status == "open"  # Unknown remote submission is not inferred as done.
