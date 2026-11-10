"""Persistent reminder outbox; system delivery acknowledgement is at-least-once."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from shared.models import Event, FetcherState, ReminderDelivery, ReminderSettings, to_naive_utc, to_aware_utc

_lock = asyncio.Lock()


def target_for(event):
    if event.deleted_at or event.status in {"done", "submitted", "closed"}:
        return None
    kind = (event.raw_payload or {}).get("kind")
    target = event.start_at if kind in {"contest", "exam"} else event.end_at
    if target is None:
        return None
    if event.all_day:
        zone = ZoneInfo(event.tz_original or "Asia/Shanghai")
        local = target.replace(tzinfo=timezone.utc) if target.tzinfo is None else target
        target = local.astimezone(zone).replace(hour=9, minute=0, second=0, microsecond=0)
    return to_naive_utc(target)


async def settings(session, now):
    value = await session.get(ReminderSettings, 1)
    if value is None:
        value = ReminderSettings(baseline_at=to_aware_utc(now))
        session.add(value)
        await session.commit()
        await session.refresh(value)
    return value


async def update_settings(session, enabled, leads, now=None):
    now = to_naive_utc(now or datetime.now(timezone.utc))
    value = await settings(session, now)
    # Every settings change starts a new baseline: no history blast on enabling.
    value.enabled, value.leads, value.baseline_at = enabled, sorted(set(leads), reverse=True), to_aware_utc(now)
    session.add(value)
    await session.commit()
    return value


async def pending(session, now=None):
    now = to_naive_utc(now or datetime.now(timezone.utc))
    async with _lock:
        cfg = await settings(session, now)
        events = list((await session.exec(select(Event))).scalars())
        states = {s.name: s for s in (await session.exec(select(FetcherState))).scalars()}
        from backend.scheduler import default_enabled
        candidates = {}
        if cfg.enabled:
            for event in events:
                state = states.get(event.platform)
                if not (state.enabled if state else default_enabled(event.platform)):
                    continue
                target = target_for(event)
                if target is None:
                    continue
                for lead in cfg.leads:
                    due = target - timedelta(minutes=lead)
                    if due < to_naive_utc(cfg.baseline_at) or due < now - timedelta(hours=24):
                        continue
                    candidates[(event.id, target, lead)] = (event, due)
        records = list((await session.exec(select(ReminderDelivery))).scalars())
        existing = {(r.event_id, to_naive_utc(r.target_at), r.lead_minutes): r for r in records}
        for key, record in existing.items():
            if record.status == "pending" and key not in candidates:
                record.status = "cancelled"
                session.add(record)
        for key, (event, due) in candidates.items():
            record = existing.get(key)
            if record is None:
                record = ReminderDelivery(event_id=key[0], target_at=to_aware_utc(key[1]), lead_minutes=key[2], due_at=to_aware_utc(due))
                session.add(record)
                records.append(record)
            elif record.status == "cancelled":
                record.status = "pending"
                session.add(record)
        await session.commit()
        by_event = {}
        for record in records:
            if record.status != "pending" or to_naive_utc(record.due_at) > now:
                continue
            key = (record.event_id, to_naive_utc(record.target_at), record.lead_minutes)
            if key not in candidates:
                continue
            event, _ = candidates[key]
            item = by_event.setdefault(event.id, {"event_id": event.id, "title": event.title,
                       "target_at": record.target_at.isoformat(), "delivery_ids": []})
            item["delivery_ids"].append(record.id)
        return list(by_event.values())


async def acknowledge(session, ids, now=None):
    now = to_naive_utc(now or datetime.now(timezone.utc))
    async with _lock:
        for identity in ids:
            record = await session.get(ReminderDelivery, identity)
            if record and record.status == "pending":
                record.status, record.sent_at = "sent", to_aware_utc(now)
                session.add(record)
        await session.commit()


async def catch_up():
    from backend import scheduler
    from backend.db import SessionLocal
    semaphore = asyncio.Semaphore(2)
    async with SessionLocal() as session:
        states = {s.name: s for s in (await session.exec(select(FetcherState))).scalars()}
    async def one(name):
        async with semaphore:
            try:
                await scheduler.run_fetcher(name, "wake")
            finally:
                async with SessionLocal() as session:
                    await pending(session)
    tasks = [asyncio.create_task(one(name)) for name in scheduler.available_fetchers()
             if scheduler.effective(name, states.get(name))[0]]
    scheduler._background.update(tasks)
    for task in tasks:
        task.add_done_callback(scheduler._background.discard)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
