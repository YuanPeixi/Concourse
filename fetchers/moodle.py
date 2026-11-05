"""Moodle calendar subscription; no SSO password or browser session is required.

python -m fetchers.moodle --save-url  # hidden prompt, existing DPAPI credential store
python -m fetchers.moodle --check     # read-only summary, no database writes
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from icalendar import Calendar

from shared import credentials
from shared.config import secrets
from shared.models import ScheduleEventCreate

NAME = "moodle"
_MAX_BYTES = 10 * 1024 * 1024


def _validate_url(value: str) -> str:
    value = value.strip()
    try:
        parts = urlsplit(value)
        query = parse_qs(parts.query)
        valid = (
            parts.scheme == "https"
            and parts.hostname
            and not parts.username
            and not parts.password
            and not parts.fragment
            and query.get("userid")
            and query.get("authtoken")
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use the HTTPS Moodle calendar export URL with userid and authtoken.")
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect may be an SSO login page. Never forward the private subscription URL.
        return None


def _download_calendar(url: str, timeout: float) -> bytes:
    # urllib does not emit httpx's INFO request logs, which include secret query parameters.
    request = Request(url, headers={"Accept": "text/calendar", "User-Agent": "Concourse/0.1"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            content = response.read(_MAX_BYTES + 1)
    except HTTPError as exc:
        raise RuntimeError(
            f"Moodle calendar HTTP {exc.code}; check the saved subscription or campus network."
        ) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError(
            "Cannot reach Moodle calendar; check the network or saved URL."
        ) from None
    if len(content) > _MAX_BYTES:
        raise ValueError("Moodle calendar exceeds the 10 MiB download limit.")
    return content


def _utc(value: date | datetime, local_zone: ZoneInfo) -> datetime:
    if not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if value.tzinfo is None:
        value = value.replace(tzinfo=local_zone)
    return value.astimezone(UTC)


def parse_calendar(
    content: bytes | str, timezone_name: str = "Asia/Shanghai"
) -> list[ScheduleEventCreate]:
    """Pure ICS parser. UID is stable across edits; missing items never imply deletion."""
    local_zone = ZoneInfo(timezone_name)
    try:
        calendar = Calendar.from_ical(content)
        if calendar.name != "VCALENDAR":
            raise ValueError
        events = []
        for component in calendar.walk("VEVENT"):
            if component.get("STATUS") == "CANCELLED":
                continue
            uid = str(component.get("UID", "")).strip()
            if not uid or "DTSTART" not in component:
                raise ValueError
            start = component.decoded("DTSTART")
            if not isinstance(start, date):
                raise ValueError
            all_day = not isinstance(start, datetime)
            if "DTEND" in component:
                end = component.decoded("DTEND")
            elif "DURATION" in component:
                end = start + component.decoded("DURATION")
            else:
                end = start + timedelta(days=1) if all_day else start
            if not isinstance(end, date) or isinstance(end, datetime) == all_day:
                raise ValueError
            start_at, end_at = _utc(start, local_zone), _utc(end, local_zone)
            if end_at < start_at:
                raise ValueError
            # DATE DTEND is exclusive; this app displays deadlines at end_at.
            if all_day:
                if end_at == start_at:
                    raise ValueError
                end_at -= timedelta(microseconds=1)
            source_id = uid
            if "RECURRENCE-ID" in component:
                recurrence_id = _utc(component.decoded("RECURRENCE-ID"), local_zone)
                source_id += "#" + recurrence_id.isoformat()
            categories = component.get("CATEGORIES", [])
            if not isinstance(categories, list):
                categories = [categories]
            courses = [str(c) for group in categories for c in group.cats]
            zone = getattr(start.tzinfo, "key", None) if not all_day else None
            if not zone:
                zone = (
                    str(start.tzinfo)
                    if isinstance(start, datetime) and start.tzinfo
                    else (timezone_name)
                )
            rrule = component.get("RRULE")
            events.append(
                ScheduleEventCreate(
                    platform=NAME,
                    source_id=source_id,
                    # Keep full category values in raw_payload; shorten only display names.
                    course=", ".join(re.sub(r"(?<=.)-[0-9a-fA-F]{32}\Z", "", c) for c in courses),
                    title=str(component.get("SUMMARY", "Moodle event")),
                    description=str(component.get("DESCRIPTION", "")),
                    location=str(component.get("LOCATION", "")),
                    meeting_url=str(component["URL"]) if "URL" in component else None,
                    start_at=start_at,
                    end_at=end_at,
                    tz_original=zone,
                    all_day=all_day,
                    # Calendar presence is not evidence of submission/completion.
                    status="open",
                    recurrence_rrule=rrule.to_ical().decode() if rrule else None,
                    raw_payload={"kind": "deadline", "categories": courses},
                )
            )
        return events
    except Exception:
        # Parser errors may quote input. Keep subscription data out of saved tracebacks.
        raise ValueError(
            "Invalid Moodle ICS calendar; check the export URL and event dates."
        ) from None


class MoodleFetcher:
    name = NAME

    def __init__(self, timezone_name: str = "Asia/Shanghai", timeout: float = 30.0):
        self.timezone_name = timezone_name
        self.timeout = timeout

    async def fetch(self, since: datetime | None = None) -> list[ScheduleEventCreate]:
        url = secrets.moodle_calendar_url.get_secret_value().strip()
        if not url:
            saved = credentials.load(NAME) or {}
            url = saved.get("calendar_url", "")
        if not url:
            raise ValueError(
                "Save a Moodle subscription with python -m fetchers.moodle --save-url."
            )
        url = _validate_url(url)
        content = await asyncio.to_thread(_download_calendar, url, self.timeout)
        # Always process the full rolling feed: old UID deadlines can have been edited.
        return parse_calendar(content, self.timezone_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--save-url", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.save_url:
            url = _validate_url(getpass.getpass("Moodle calendar URL (hidden): "))
            credentials.save(NAME, {"calendar_url": url})
            print("Moodle subscription saved. Run --check to verify without database writes.")
        else:
            events = asyncio.run(MoodleFetcher().fetch())
            deadlines = [e.end_at for e in events if e.end_at]
            print(
                json.dumps(
                    {
                        "platform": NAME,
                        "event_count": len(events),
                        "earliest_end_utc": min(deadlines).isoformat() if deadlines else None,
                        "latest_end_utc": max(deadlines).isoformat() if deadlines else None,
                    }
                )
            )
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
