from __future__ import annotations

import io
import traceback
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from fetchers import moodle
from shared.models import compute_content_hash

PRIVATE_URL = (
    "https://moodle.example/calendar/export_execute.php?userid=1&authtoken=test-private-token"
    "&preset_what=all&preset_time=recentupcoming"
)


def calendar(*events: str) -> bytes:
    body = "\r\n".join("BEGIN:VEVENT\r\n" + e + "\r\nEND:VEVENT" for e in events)
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n" + body + "\r\nEND:VCALENDAR\r\n"
    ).encode()


def event(**changes) -> str:
    fields = {
        "UID": "stable-event@example",
        "SUMMARY": "Homework",
        "DTSTART": "20261105T160000Z",
        "DTEND": "20261105T160000Z",
    }
    fields.update(changes)
    return "\r\n".join(f"{k}:{v}" for k, v in fields.items() if v is not None)


def test_moodle_deadline_metadata_and_no_inferred_completion():
    content = calendar(
        event(
            SUMMARY="作业一",
            CATEGORIES=r"课程\,一,课程二",
            DESCRIPTION=r"First line\nSecond line",
            LOCATION="Room 1",
        )
    )
    (item,) = moodle.parse_calendar(content)
    assert item.platform == "moodle"
    assert item.source_id == "stable-event@example"
    assert item.title == "作业一"
    assert item.course == "课程,一, 课程二"
    assert item.description == "First line\nSecond line"
    assert item.location == "Room 1"
    assert item.start_at == item.end_at == datetime(2026, 11, 5, 16, tzinfo=UTC)
    assert item.status == "open" and item.meeting_url is None
    assert item.raw_payload["kind"] == "deadline"


def test_unfolded_unicode_and_url():
    content = calendar(
        event(SUMMARY="A long\r\n  title", URL="https://moodle.example/mod/assign/view.php?id=2")
    )
    (item,) = moodle.parse_calendar(content)
    assert item.title == "A long title"
    assert item.meeting_url.endswith("id=2")


@pytest.mark.parametrize(
    "start_key,start,end_key,end,expected",
    [
        ("DTSTART", "20261106T000000", "DTEND", "20261106T010000", 17),
        (
            "DTSTART;TZID=Asia/Shanghai",
            "20261106T000000",
            "DTEND;TZID=Asia/Shanghai",
            "20261106T010000",
            17,
        ),
        (
            "DTSTART;TZID=America/New_York",
            "20261106T000000",
            "DTEND;TZID=America/New_York",
            "20261106T010000",
            6,
        ),
    ],
)
def test_timezones(start_key, start, end_key, end, expected):
    fields = {"DTSTART": None, "DTEND": None, start_key: start, end_key: end}
    content = calendar(event(**fields))
    (item,) = moodle.parse_calendar(content)
    assert item.end_at.hour == expected and item.end_at.utcoffset().total_seconds() == 0


def test_all_day_exclusive_end_and_missing_end():
    (item,) = moodle.parse_calendar(
        calendar(
            event(
                DTSTART=None,
                DTEND=None,
                **{"DTSTART;VALUE=DATE": "20261106", "DTEND;VALUE=DATE": "20261108"},
            )
        )
    )
    assert item.all_day
    assert item.start_at == datetime(2026, 11, 5, 16, tzinfo=UTC)
    assert item.end_at == datetime(2026, 11, 7, 15, 59, 59, 999999, tzinfo=UTC)
    (item,) = moodle.parse_calendar(
        calendar(event(DTSTART=None, DTEND=None, **{"DTSTART;VALUE=DATE": "20261106"}))
    )
    assert item.end_at == datetime(2026, 11, 6, 15, 59, 59, 999999, tzinfo=UTC)


def test_duration_and_missing_timed_end():
    (item,) = moodle.parse_calendar(calendar(event(DTEND=None, DURATION="PT2H")))
    assert item.end_at.hour == 18
    (item,) = moodle.parse_calendar(calendar(event(DTEND=None)))
    assert item.start_at == item.end_at


def test_uid_stable_across_deadline_edits():
    (old,) = moodle.parse_calendar(calendar(event()))
    (new,) = moodle.parse_calendar(calendar(event(DTEND="20261106T160000Z")))
    assert old.source_id == new.source_id
    assert compute_content_hash(old) != compute_content_hash(new)


def test_cancelled_and_empty_feeds():
    assert moodle.parse_calendar(calendar()) == []
    assert moodle.parse_calendar(calendar(event(STATUS="CANCELLED"))) == []


def test_recurrence_instances_have_distinct_ids():
    items = moodle.parse_calendar(
        calendar(
            event(RRULE="FREQ=WEEKLY"),
            event(**{"RECURRENCE-ID": "20261112T160000Z"}),
        )
    )
    assert items[0].recurrence_rrule == "FREQ=WEEKLY"
    assert items[0].source_id != items[1].source_id


@pytest.mark.parametrize(
    "content",
    [
        b"<html>SSO login</html>",
        b"not a calendar",
        calendar(event(UID=None)),
        calendar(event(DTSTART=None)),
        calendar(event(DTEND="20261104T160000Z")),
        calendar(event(DTSTART="invalid")),
        calendar(event(DTEND=None, **{"DTEND;VALUE=DATE": "20261107"})),
    ],
)
def test_invalid_feeds_fail_without_returning_partial_results(content):
    with pytest.raises(ValueError, match="Invalid Moodle ICS"):
        moodle.parse_calendar(content)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://moodle.example/?userid=1&authtoken=x",
        "https://moodle.example/login",
        "https://user:pass@moodle.example/?userid=1&authtoken=x",
        PRIVATE_URL + "#fragment",
    ],
)
def test_bad_urls_never_echo_private_input(url):
    with pytest.raises(ValueError) as caught:
        moodle._validate_url(url)
    assert "test-private-token" not in str(caught.value)


@pytest.mark.parametrize(
    "error",
    [
        HTTPError(PRIVATE_URL, 302, "redirect", {}, None),
        HTTPError(PRIVATE_URL, 403, "forbidden", {}, None),
        URLError(PRIVATE_URL),
        TimeoutError(PRIVATE_URL),
    ],
)
def test_network_errors_hide_secrets(monkeypatch, error):
    class Opener:
        def open(self, request, timeout):
            raise error

    monkeypatch.setattr(moodle, "build_opener", lambda handler: Opener())
    with pytest.raises(RuntimeError) as caught:
        moodle._download_calendar(PRIVATE_URL, 30)
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert "test-private-token" not in rendered


def test_download_and_size_limit(monkeypatch):
    content = calendar(event())

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == PRIVATE_URL
            assert timeout == 12
            return io.BytesIO(content)

    def opener(handler):
        assert handler.redirect_request(None, None, 302, "", {}, "https://sso.example") is None
        return Opener()

    monkeypatch.setattr(moodle, "build_opener", opener)
    assert moodle._download_calendar(PRIVATE_URL, 12) == content
    monkeypatch.setattr(moodle, "_MAX_BYTES", 2)
    with pytest.raises(ValueError, match="download limit"):
        moodle._download_calendar(PRIVATE_URL, 12)


@pytest.mark.asyncio
async def test_fetch_secret_precedence_and_full_feed(monkeypatch):
    monkeypatch.setattr(moodle.secrets, "moodle_calendar_url", SecretStr(PRIVATE_URL))
    monkeypatch.setattr(moodle.credentials, "load", lambda _: pytest.fail("Unexpected DPAPI read"))
    monkeypatch.setattr(moodle, "_download_calendar", lambda u, t: calendar(event()))
    events = await moodle.MoodleFetcher().fetch(since=datetime(2027, 1, 1, tzinfo=UTC))
    assert len(events) == 1
    assert "test-private-token" not in repr(moodle.secrets)


@pytest.mark.asyncio
async def test_saved_credentials_and_missing_configuration(monkeypatch):
    monkeypatch.setattr(moodle.secrets, "moodle_calendar_url", SecretStr(""))
    monkeypatch.setattr(moodle.credentials, "load", lambda _: {"calendar_url": PRIVATE_URL})
    monkeypatch.setattr(moodle, "_download_calendar", lambda u, t: calendar(event()))
    assert len(await moodle.MoodleFetcher().fetch()) == 1
    monkeypatch.setattr(moodle.credentials, "load", lambda _: None)
    with pytest.raises(ValueError, match="--save-url"):
        await moodle.MoodleFetcher().fetch()


@pytest.mark.asyncio
async def test_repository_upsert_preserves_done_and_does_not_duplicate():
    from backend.repository import upsert_event

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            (first,) = moodle.parse_calendar(calendar(event()))
            stored, changed = await upsert_event(session, first)
            assert changed
            event_id = stored.id
            stored.status = "done"
            session.add(stored)
            await session.commit()
            (updated,) = moodle.parse_calendar(calendar(event(DTEND="20261106T160000Z")))
            stored, changed = await upsert_event(session, updated)
            assert changed and stored.id == event_id and stored.status == "done"
            assert stored.end_at.replace(tzinfo=UTC) == updated.end_at
            stored, changed = await upsert_event(session, updated)
            assert not changed and stored.id == event_id
    finally:
        await engine.dispose()
