"""FastAPI 应用入口。"""

from __future__ import annotations

import hmac
import os

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, ValidationError, StrictInt, StrictBool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db import init_db
from backend.repository import (
    accept_staged,
    create_staged,
    delete_event,
    dismiss_staged,
    get_session,
    list_events,
    list_fetcher_states,
    list_staged,
    list_trash,
    mark_done,
    permanent_delete_event,
    restore_event,
    update_fetcher_settings,
)
from shared.config import secrets
from shared.version import __version__
from shared.platforms import REGISTRY, configured, validate_credentials
from shared.models import Event, FetcherState, StagedEvent, SubmissionPolicy, to_aware_utc

bearer = HTTPBearer(auto_error=False)


def _require_agent_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
) -> None:
    if not secrets.agent_token:
        raise HTTPException(503, "AGENT_TOKEN not configured")
    if credentials is None or credentials.credentials != secrets.agent_token:
        raise HTTPException(401, "Invalid or missing agent token")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await init_db()
    from backend.scheduler import start_scheduler, stop_scheduler
    await start_scheduler()
    try:
        yield
    finally:
        await stop_scheduler()
        from backend.db import engine
        await engine.dispose()


def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
) -> None:
    token = os.environ.get("CONCOURSE_DESKTOP_TOKEN") or secrets.agent_token
    if not token:
        raise HTTPException(503, "API token is not configured")
    if credentials is None or not hmac.compare_digest(credentials.credentials, token):
        raise HTTPException(401, "Unauthorized")


app = FastAPI(title="Concourse", version=__version__, lifespan=_lifespan,
              dependencies=[Depends(require_api_token)], docs_url=None, redoc_url=None,
              openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"]
    if os.environ.get("CONCOURSE_DESKTOP") != "1" else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

Session = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@app.get("/events", response_model=list[Event])
async def get_events(
    session: Session,
    platforms: str | None = None,      # comma-separated
    status: str | None = None,
    end_after: datetime | None = None,
    end_before: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
):
    platform_list = [p.strip() for p in platforms.split(",")] if platforms else None
    return await list_events(
        session,
        platforms=platform_list,
        status=status,
        end_after=end_after,
        end_before=end_before,
        limit=limit,
        offset=offset,
    )


@app.get("/events/{event_id}", response_model=Event)
async def get_event_detail(event_id: int, session: Session):
    event = await session.get(Event, event_id)
    if event is None or event.deleted_at:
        raise HTTPException(404, "事项已删除或不存在")
    return event


@app.delete("/events/{event_id}", status_code=204)
async def delete_event_route(event_id: int, session: Session):
    ok = await delete_event(session, event_id)
    if not ok:
        raise HTTPException(404, "Event not found")


@app.post("/events/{event_id}/done", response_model=Event)
async def mark_event_done(event_id: int, session: Session):
    event = await mark_done(session, event_id)
    if event is None:
        raise HTTPException(404, "Event not found or already deleted")
    return event


@app.post("/events/{event_id}/restore", response_model=Event)
async def restore_event_route(event_id: int, session: Session):
    event = await restore_event(session, event_id)
    if event is None:
        raise HTTPException(404, "Event not found or not in trash")
    return event


@app.delete("/events/{event_id}/permanent", status_code=204)
async def permanent_delete_route(event_id: int, session: Session):
    ok = await permanent_delete_event(session, event_id)
    if not ok:
        raise HTTPException(404, "Event not found in trash")


@app.get("/trash", response_model=list[Event])
async def get_trash(session: Session, limit: int = 200):
    return await list_trash(session, limit=limit)


# ---------------------------------------------------------------------------
# Staged (暂存区)
# ---------------------------------------------------------------------------
@app.get("/staged", response_model=list[StagedEvent])
async def get_staged(session: Session):
    return await list_staged(session)


@app.post("/staged/{staged_id}/accept", response_model=Event)
async def accept_staged_event(staged_id: int, session: Session):
    event = await accept_staged(session, staged_id)
    if event is None:
        raise HTTPException(404, "Staged event not found")
    return event


@app.delete("/staged/{staged_id}", status_code=204)
async def dismiss_staged_event(staged_id: int, session: Session):
    ok = await dismiss_staged(session, staged_id)
    if not ok:
        raise HTTPException(404, "Staged event not found")


# ---------------------------------------------------------------------------
# Fetchers（启停、频率、运行状态、立即触发）
# ---------------------------------------------------------------------------
class FetcherInfo(BaseModel):
    name: str
    enabled: bool
    cron: str
    cron_default: str
    cron_overridden: bool
    running: bool
    next_run_at: datetime | None
    config: dict
    description: dict
    configured: bool
    credentials_saved: bool
    state: str
    error_kind: str | None
    last_trigger: str | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_success_at: datetime | None = None
    last_status: str | None = None
    last_error: str = ""
    last_count: int = 0
    last_changed: int = 0
    last_duration_ms: int = 0


class FetcherPatch(BaseModel):
    enabled: bool | None = None
    cron: str | None = None  # 显式传 null 或 "" 恢复 config.yaml 默认值


def _fetcher_info(name: str, state: FetcherState | None) -> FetcherInfo:
    from backend import scheduler as sch
    from shared import config as cfg, credentials

    enabled, cron = sch.effective(name, state)
    from backend import login
    from shared.errors import stored_kind, MESSAGES
    s = state or FetcherState(name=name)
    is_configured = configured(name)
    kind = stored_kind(s.last_error)
    status = ("waiting_verification" if login.waiting(name) else "fetching" if sch.is_running(name)
              else "unconfigured" if not is_configured else "failed" if s.last_status == "error" else "idle")
    return FetcherInfo(
        name=name,
        enabled=enabled,
        cron=cron,
        cron_default=cfg.schedule_for(name),
        cron_overridden=bool(state and state.cron),
        running=sch.is_running(name),
        next_run_at=sch.next_run_at(name) if enabled else None,
        config=REGISTRY[name].options.model_validate(cfg.fetcher_kwargs(name)).model_dump(),
        description=REGISTRY[name].describe(),
        configured=is_configured,
        state=status,
        error_kind=kind,
        credentials_saved=credentials.has(name),
        last_trigger=s.last_trigger,
        last_started_at=to_aware_utc(s.last_started_at),
        last_finished_at=to_aware_utc(s.last_finished_at),
        last_success_at=to_aware_utc(s.last_success_at),
        last_status=s.last_status,
        last_error=MESSAGES.get(kind, MESSAGES["unknown"]) if s.last_error else "",
        last_count=s.last_count,
        last_changed=s.last_changed,
        last_duration_ms=s.last_duration_ms,
    )


def _require_fetcher(name: str) -> None:
    from backend.scheduler import available_fetchers

    if name not in available_fetchers():
        raise HTTPException(404, f"Unknown fetcher: {name}")


@app.get("/fetchers", response_model=list[FetcherInfo])
async def get_fetchers(session: Session):
    from backend.scheduler import available_fetchers

    states = {s.name: s for s in await list_fetcher_states(session)}
    return [_fetcher_info(n, states.get(n)) for n in available_fetchers()]


@app.patch("/fetchers/{name}", response_model=FetcherInfo)
async def patch_fetcher(name: str, body: FetcherPatch, session: Session):
    from backend import scheduler as sch

    _require_fetcher(name)
    if body.enabled and not configured(name):
        raise HTTPException(422, "请先保存完整的平台配置与凭据")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("enabled") is None:
        changes.pop("enabled", None)
    if changes.get("cron"):
        changes["cron"] = changes["cron"].strip()
        try:
            sch.validate_cron(changes["cron"])
        except ValueError as exc:
            raise HTTPException(422, f"无效的 cron 表达式: {exc}") from exc
    state = await update_fetcher_settings(
        session, name, default_enabled=sch.default_enabled(name), changes=changes,
    )
    await sch.apply_settings(name)
    return _fetcher_info(name, state)


@app.post("/fetchers/{name}/run", status_code=202)
async def run_fetcher_now(name: str):
    from backend.scheduler import trigger_now

    _require_fetcher(name)
    if not trigger_now(name):
        raise HTTPException(409, "Fetcher is already running")
    return {"started": True}


class PlatformSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict = {}
    credentials: dict[str, str] | None = None
    clear_credentials: bool = False


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # FastAPI's default validation response may echo a password or private URL.
    return JSONResponse(status_code=422, content={"detail": "请求格式无效，请检查字段类型"})


@app.put("/fetchers/{name}/config", response_model=FetcherInfo)
async def save_platform(name: str, body: PlatformSettings, session: Session):
    from shared import config as cfg, credentials
    from backend import scheduler as sch
    from backend.repository import get_fetcher_state

    _require_fetcher(name)
    if sch.is_running(name) or name in sch._pending:
        raise HTTPException(409, "平台正在运行，请结束后再修改配置")
    try:
        options = REGISTRY[name].options.model_validate(body.config).model_dump()
        if body.credentials is not None:
            validate_credentials(name, body.credentials)
    except (ValueError, ValidationError):
        raise HTTPException(422, "配置无效，请检查课程 ID、选项或完整凭据") from None
    if body.credentials is not None and body.clear_credentials:
        raise HTTPException(422, "不能同时保存和清除凭据")
    if body.credentials is not None:
        credentials.save(name, body.credentials)
    if body.clear_credentials:
        credentials.forget(name)
    cfg.save_fetcher_config(name, options)
    await sch._record(name, last_error="", last_status=None)
    if not configured(name):
        await update_fetcher_settings(session, name, default_enabled=False, changes={"enabled": False})
        await sch.apply_settings(name)
    return _fetcher_info(name, await get_fetcher_state(session, name))


@app.post("/fetchers/{name}/login/{action}")
async def login_action(name: str, action: str):
    from backend import login
    _require_fetcher(name)
    if not REGISTRY[name].login or action not in {"start", "complete", "cancel"}:
        raise HTTPException(422, "不支持的登录操作")
    if action == "start":
        if not login.start(name):
            raise HTTPException(409, "平台任务正在运行")
    elif not login.control(name, action):
        raise HTTPException(409, "当前没有辅助登录任务")
    return {"accepted": True}


@app.post("/fetchers/{name}/check")
async def check_platform(name: str):
    _require_fetcher(name)
    if name != "moodle" or not configured(name):
        raise HTTPException(422, "请先保存 Moodle 订阅地址")
    import asyncio
    from fetchers.moodle import MoodleFetcher
    try:
        async with asyncio.timeout(35):
            events = await MoodleFetcher().fetch()
        return {"ok": True, "count": len(events)}
    except Exception:
        raise HTTPException(422, "连接验证失败，请检查订阅地址或网络后重试") from None


class ReminderOptions(BaseModel):
    enabled: StrictBool = True
    leads: list[StrictInt] = [1440, 60]


@app.get("/reminders/settings")
async def reminder_settings(session: Session):
    from backend.reminders import settings
    return await settings(session, datetime.now(timezone.utc).replace(tzinfo=None))


@app.put("/reminders/settings")
async def save_reminder_settings(body: ReminderOptions, session: Session):
    from backend.reminders import update_settings
    if not 1 <= len(body.leads) <= 5 or any(isinstance(x, bool) or x < 0 or x > 10080 for x in body.leads):
        raise HTTPException(422, "提前量需为 0 到 10080 分钟，最多 5 项")
    return await update_settings(session, body.enabled, body.leads)


@app.get("/notifications/pending")
async def pending_notifications(session: Session):
    from backend.reminders import pending
    return await pending(session)


@app.post("/notifications/ack")
async def acknowledge_notifications(ids: list[int], session: Session):
    from backend.reminders import acknowledge
    if len(ids) > 100:
        raise HTTPException(422, "Too many records")
    await acknowledge(session, ids)
    return {"ok": True}


@app.post("/runtime/wake", status_code=202)
async def runtime_wake():
    import asyncio
    from backend import scheduler
    from backend.reminders import catch_up
    task = asyncio.create_task(catch_up())
    scheduler._background.add(task)
    task.add_done_callback(scheduler._background.discard)
    return {"started": True}


# ---------------------------------------------------------------------------
# Agent endpoint
# ---------------------------------------------------------------------------
class AgentRequest(Exception):
    pass


class AgentQuery(BaseModel):
    message: str


class AgentResponse(BaseModel):
    reply: str
    staged_ids: list[int] = []


@app.post("/agent", response_model=AgentResponse, dependencies=[Depends(_require_agent_token)])
async def agent(body: AgentQuery, session: Session):
    from backend.services.agent import handle_agent_message
    return await handle_agent_message(body.message, session)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__, "utc": datetime.now(timezone.utc).isoformat()}
