"""事件 repository：upsert、查询、暂存区操作。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db import SessionLocal
from shared.models import (
    Event,
    FetcherState,
    ScheduleEventCreate,
    StagedEvent,
    SubmissionPolicy,
    compute_content_hash,
    to_aware_utc,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
async def upsert_event(session: AsyncSession, ev: ScheduleEventCreate) -> tuple[Event, bool]:
    """Upsert 一条事项；返回 (event, changed)。"""
    content_hash = compute_content_hash(ev)
    stmt = select(Event).where(Event.platform == ev.platform, Event.source_id == ev.source_id)
    result = await session.exec(stmt)  # type: ignore[attr-defined]
    existing: Event | None = result.scalars().first()

    now = datetime.now(timezone.utc)
    if existing is not None:
        changed = existing.content_hash != content_hash
        existing.last_seen_at = now
        # raw_payload 不参与 content_hash（只是元数据），但始终同步为最新抓取结果，
        # 否则 fetcher 新增的字段（如 kind）永远到不了已入库的行
        existing.raw_payload = ev.raw_payload
        if changed:
            existing.title = ev.title
            existing.description = ev.description
            existing.location = ev.location
            existing.meeting_url = ev.meeting_url
            existing.course = ev.course
            existing.start_at = to_aware_utc(ev.start_at)
            existing.end_at = to_aware_utc(ev.end_at)
            existing.tz_original = ev.tz_original
            existing.all_day = ev.all_day
            # 用户手动标记的 done 不被抓取回来的 open/unknown 覆盖
            if not (existing.status == "done" and ev.status in ("open", "unknown")):
                existing.status = ev.status
            existing.submission_policy = ev.submission_policy
            existing.organizer_name = ev.organizer_name
            existing.organizer_email = ev.organizer_email
            existing.attendees = ev.attendees
            existing.recurrence_rrule = ev.recurrence_rrule
            existing.content_hash = content_hash
            existing.updated_at = now
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing, changed

    new_event = Event(
        platform=ev.platform,
        source_id=ev.source_id,
        course=ev.course,
        title=ev.title,
        description=ev.description,
        location=ev.location,
        meeting_url=ev.meeting_url,
        start_at=to_aware_utc(ev.start_at),
        end_at=to_aware_utc(ev.end_at),
        tz_original=ev.tz_original,
        all_day=ev.all_day,
        status=ev.status,
        submission_policy=ev.submission_policy,
        organizer_name=ev.organizer_name,
        organizer_email=ev.organizer_email,
        attendees=ev.attendees,
        recurrence_rrule=ev.recurrence_rrule,
        content_hash=content_hash,
        raw_payload=ev.raw_payload,
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
    )
    session.add(new_event)
    await session.commit()
    await session.refresh(new_event)
    return new_event, True


async def list_events(
    session: AsyncSession,
    *,
    platforms: list[str] | None = None,
    status: str | None = None,
    end_before: datetime | None = None,
    end_after: datetime | None = None,
    include_deleted: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[Event]:
    stmt = select(Event)
    if not include_deleted:
        stmt = stmt.where(Event.deleted_at.is_(None))
    if platforms:
        stmt = stmt.where(Event.platform.in_(platforms))
    if status:
        stmt = stmt.where(Event.status == status)
    if end_after:
        stmt = stmt.where(Event.end_at >= to_aware_utc(end_after))
    if end_before:
        stmt = stmt.where(Event.end_at <= to_aware_utc(end_before))
    stmt = stmt.order_by(Event.end_at.asc().nulls_last()).offset(offset).limit(limit)
    result = await session.exec(stmt)  # type: ignore[attr-defined]
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Staged events (Agent / CLI 暂存区)
# ---------------------------------------------------------------------------
async def create_staged(
    session: AsyncSession,
    title: str,
    end_at: datetime | None,
    *,
    platform: str = "manual",
    course: str = "",
    start_at: datetime | None = None,
    description: str = "",
    submission_policy: SubmissionPolicy = SubmissionPolicy.NONE,
    proposed_by: str = "agent",
    raw_payload: dict | None = None,
) -> StagedEvent:
    import uuid
    staged = StagedEvent(
        platform=platform,
        source_id=str(uuid.uuid4()),
        course=course,
        title=title,
        description=description,
        start_at=to_aware_utc(start_at),
        end_at=to_aware_utc(end_at),
        submission_policy=submission_policy,
        proposed_by=proposed_by,
        raw_payload=raw_payload or {},
    )
    session.add(staged)
    await session.commit()
    await session.refresh(staged)
    return staged


async def delete_event(session: AsyncSession, event_id: int) -> bool:
    """软删除：设置 deleted_at，保留记录，可通过 restore_event 恢复。"""
    event = await session.get(Event, event_id)
    if event is None:
        return False
    event.deleted_at = datetime.now(timezone.utc)
    session.add(event)
    await session.commit()
    return True


async def restore_event(session: AsyncSession, event_id: int) -> Event | None:
    """从回收站恢复：清除 deleted_at。"""
    event = await session.get(Event, event_id)
    if event is None or event.deleted_at is None:
        return None
    event.deleted_at = None
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def permanent_delete_event(session: AsyncSession, event_id: int) -> bool:
    """硬删除：仅用于回收站内已软删除的条目。"""
    event = await session.get(Event, event_id)
    if event is None or event.deleted_at is None:
        return False
    await session.delete(event)
    await session.commit()
    return True


async def mark_done(session: AsyncSession, event_id: int) -> Event | None:
    """将事项状态标记为 done（已完成），不移入回收站。"""
    event = await session.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        return None
    event.status = "done"
    event.updated_at = datetime.now(timezone.utc)
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def list_trash(
    session: AsyncSession,
    limit: int = 200,
) -> list[Event]:
    """返回回收站中的事项（deleted_at IS NOT NULL），按删除时间倒序。"""
    stmt = (
        select(Event)
        .where(Event.deleted_at.is_not(None))
        .order_by(Event.deleted_at.desc())
        .limit(limit)
    )
    result = await session.exec(stmt)  # type: ignore[attr-defined]
    return list(result.scalars().all())


async def list_staged(session: AsyncSession) -> list[StagedEvent]:
    result = await session.exec(select(StagedEvent).order_by(StagedEvent.created_at.asc()))  # type: ignore[attr-defined]
    return list(result.scalars().all())


async def accept_staged(session: AsyncSession, staged_id: int) -> Event | None:
    """暂存条目升级为正式事项，保留原 platform 和 course。"""
    staged = await session.get(StagedEvent, staged_id)
    if staged is None:
        return None
    ev = ScheduleEventCreate(
        platform=staged.platform,
        source_id=staged.source_id,
        course=getattr(staged, 'course', ''),
        title=staged.title,
        description=staged.description,
        start_at=staged.start_at,
        end_at=staged.end_at,
        tz_original=staged.tz_original,
        all_day=staged.all_day,
        status=staged.status,
        submission_policy=staged.submission_policy,
        raw_payload=staged.raw_payload or {},
    )
    event, _ = await upsert_event(session, ev)
    await session.delete(staged)
    await session.commit()
    return event


async def dismiss_staged(session: AsyncSession, staged_id: int) -> bool:
    staged = await session.get(StagedEvent, staged_id)
    if staged is None:
        return False
    await session.delete(staged)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# Fetcher 状态
# ---------------------------------------------------------------------------
async def get_fetcher_state(session: AsyncSession, name: str) -> FetcherState | None:
    return await session.get(FetcherState, name)


async def list_fetcher_states(session: AsyncSession) -> list[FetcherState]:
    result = await session.exec(select(FetcherState))  # type: ignore[attr-defined]
    return list(result.scalars().all())


async def update_fetcher_settings(
    session: AsyncSession,
    name: str,
    *,
    default_enabled: bool,
    changes: dict,
) -> FetcherState:
    """changes 只含调用方显式提供的键：enabled / cron（cron=None 表示恢复默认）。"""
    state = await session.get(FetcherState, name)
    if state is None:
        state = FetcherState(name=name, enabled=default_enabled)
    if "enabled" in changes:
        state.enabled = changes["enabled"]
    if "cron" in changes:
        state.cron = changes["cron"] or None
    session.add(state)
    await session.commit()
    await session.refresh(state)
    return state
