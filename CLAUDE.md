# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Concourse** — multi-platform deadline/schedule aggregation service. Each platform has an isolated fetcher that scrapes data and emits normalized `ScheduleEventCreate` objects. A FastAPI backend aggregates these into SQLite, exposes a REST API, and drives an agent-friendly endpoint. A CLI (`concourse`) handles day-to-day operations. A Vue 3 frontend (`frontend/`) provides the dashboard, timeline, fetcher management and trash views.

## Commands

```
uv sync                                           # install / sync deps
uv run uvicorn backend.main:app --reload          # dev server (port 8000)
uv run pytest                                     # full test suite
uv run pytest tests/test_scholat_parser.py -v     # single file
uv run python -m fetchers.codeforces              # run a fetcher standalone
uv run python -m fetchers.scholat --save-credentials
uv run python scripts/debug_scholat.py            # read-only: dump raw HTML + parsed JSON to .debug/scholat/, no DB writes
concourse list                                    # after uv sync + uv pip install -e .
```

CLI add flags (since initial scaffold):
```
concourse add --title "作业" --end 1028+ --platform scholat --course "Java程序设计"
```
`--platform` defaults to `manual`; `--course` defaults to empty.

Frontend (Vue 3 + Vite + Pinia, in `frontend/`):
```
npm run dev / npm run build / npm run type-check
```

## Directory structure

```
shared/
  models.py        ScheduleEventCreate, Event, StagedEvent, FetcherState, compute_content_hash
  credentials.py   DPAPI read/write (pure ctypes, no pywin32)
  config.py        load config.yaml + .env secrets via pydantic-settings
fetchers/
  base.py          Fetcher Protocol (runtime_checkable)
  codeforces.py    pure-HTTP reference fetcher (no login)
  scholat.py       Playwright fetcher + parse_homework_list/detail pure fns
backend/
  db.py            SQLModel async engine + init_db()
  repository.py    upsert_event, list_events, staged CRUD, trash, fetcher state
  scheduler.py     APScheduler cron wrapper; run_fetcher records results; hot-reload via apply_settings
  main.py          FastAPI app, routes (/events /staged /trash /fetchers /agent), lifespan (starts scheduler)
  services/
    agent.py       Anthropic tool-use loop, action whitelist
concourse/
  cli.py           argparse CLI: list/add/staged/accept/dismiss/done/delete/restore/trash/fetch/creds
frontend/src/
  platforms.ts     platform display names + colours (single source for all views)
  stores/          events.ts (events/staged/trash), fetchers.ts (fetcher management)
  views/           Dashboard, Timeline, Fetchers, Trash
tests/
  test_cli_time.py        parse_shorthand unit tests
  test_scholat_parser.py  parse_homework_list/detail offline tests
  test_fetchers_api.py    /fetchers API (temp DB, fake fetcher)
config.yaml        non-secret config (fetcher params, cron, agent whitelist)
.env               secrets (DATABASE_URL, AGENT_TOKEN, ANTHROPIC_API_KEY)
```

## Architecture

### Data model

`ScheduleEventCreate` is the fetcher output contract. `Event` is the persisted form (SQLite, naive UTC datetimes). `StagedEvent` holds agent/CLI proposals pending human accept/dismiss. Unique key is `(platform, source_id)`. Upserts compare `content_hash` (sha256 of normalised fields) to detect changes.

`course` is the platform-internal sub-classification (e.g. a course name under scholat: "Java程序设计"). It is indexed and used by the frontend to group within a platform. Fetchers SHOULD set it when the source distinguishes courses; manual/CLI additions default to empty.

All datetimes stored as naive UTC. `to_naive_utc` / `to_aware_utc` helpers in `shared/models.py` handle conversion at boundaries.

### Fetcher contract

Every fetcher implements `Fetcher` (Protocol in `fetchers/base.py`):
```python
class MyFetcher:
    name = "myplatform"
    async def fetch(self, since=None) -> list[ScheduleEventCreate]: ...
```

`scheduler.py` discovers fetchers by name via `importlib.import_module("fetchers.<name>")` and instantiates `<Name>Fetcher(**config_kwargs)`. Adding a platform = one file + one entry in `config.yaml`.

### Fetcher scheduling & management

Known fetchers = keys of `config.yaml` `schedule`/`fetchers` ∪ modules in `fetchers/` (excluding `_*` and `base`). A fetcher module with no `schedule` entry shows up disabled by default.

Runtime settings live in the `fetcher_states` table and override `config.yaml`: `enabled` and `cron` (`NULL` = use `config.yaml` `schedule.<name>`, falling back to hourly). The same row records the last run: trigger (`schedule`/`manual`/`cli`), start/finish/success times, `ok`/`error`, error message, count, changed count, duration. Fetcher constructor kwargs are still read-only from `config.yaml` (restart to change).

Triggers, all through `run_fetcher(name, trigger)` (per-name in-process lock; a concurrent trigger is skipped):
- cron jobs registered by `start_scheduler()`; `apply_settings(name)` rebuilds a job after a settings change, no restart needed
- `POST /fetchers/{name}/run` (202, runs in background; 409 if already running)
- `concourse fetch <name>` (runs in the CLI process — its lock does not see the backend's)

API: `GET /fetchers`, `PATCH /fetchers/{name}` `{enabled?, cron?}` (`cron: null` resets to default; invalid cron → 422), `POST /fetchers/{name}/run`. Frontend: 抓取器 tab (`FetchersView.vue`).

Fetcher results are upserted directly into `events` (they are the platform's source of truth) — the staged buffer only applies to agent/CLI additions.

`raw_payload.kind` tells the frontend which time matters: `contest` (codeforces) / `exam` (itestcloud) are *timed* — displayed, sorted and urgency-ranked by `start_at` (shown as 进行中 while inside `[start_at, end_at)`). Anything else (`train`, scholat homework, manual) is a deadline → `end_at`. Logic lives in `frontend/src/eventTime.ts`. `raw_payload` is not part of `content_hash`, but `upsert_event` always refreshes it (without counting it as changed), so new payload fields reach existing rows on the next fetch.

**Playwright fetchers** use `launch_persistent_context` to preserve login sessions across runs (`.browser-data/<platform>/`). Scraping logic is split: `scrape` navigates and fetches HTML; `parse_*` are pure functions on HTML strings — the latter are unit-testable offline. See `fetchers/scholat.py` as the reference.

**HTTP fetchers** use `httpx.AsyncClient` with no browser. See `fetchers/codeforces.py`.

### Credential storage (DPAPI)

`shared/credentials.py` — pure ctypes, no pywin32. Payload is any JSON dict (username+password, tokens, etc.). Stored at `%LOCALAPPDATA%\concourse\credentials\<platform>.dpapi`. Bound to the current Windows user; cannot be decrypted on another machine or account. Non-Windows falls back to chmod-600 plaintext with a warning.

CLI: `concourse creds set <platform>` / `concourse creds forget <platform>`

### Agent endpoint

`POST /agent` requires `Authorization: Bearer <AGENT_TOKEN>`. Runs an Anthropic tool-use loop. Allowed actions are declared in `config.yaml agent.allowed_actions`; any tool call not in that list is rejected before dispatch. Write operations (`draft_add_event`) only produce `StagedEvent` proposals — they never write directly to `events`. All staged entries require explicit `accept` via `/staged/{id}/accept` or `concourse accept <id>`.

### CLI time shorthand

`mmdd+` = that day 23:59 CST → UTC (deadline end-of-day).  
`mmdd-` = that day 00:00 CST → UTC (start-of-day marker).  
Bare `mmdd` (no suffix) is rejected to avoid ambiguity.  
Full ISO 8601 strings are also accepted.

## Key decisions

- **No cross-platform deduplication** — each event is its platform's record; the aggregated view shows all platforms side-by-side without merging.
- **SQLite first** — no Docker for local dev. Switch to PostgreSQL: change `DATABASE_URL` to `postgresql+asyncpg://...` and `uv add asyncpg`.
- **DPAPI single-machine** — intentional for Windows Server home deployment over Tailscale. If porting to Linux, swap to `keyring` library.
- **Staged buffer for all manual writes** — agent and CLI never write directly to `events`; every addition goes through `staged_events` → human confirm. Fetchers are the exception (see above).
- **Soft delete + done are orthogonal** — `status='done'` marks an event finished but keeps it in the main list (with a strike-through). `deleted_at` (non-null) moves it to the trash, invisible to `list_events` by default. Done and deleted are independent: a done event can be trashed, a trashed event can be restored to its pre-delete `status` without remembering prior state. Permanent deletion (hard `DELETE`) only applies to already-soft-deleted rows.

### Frontend tab layout

The Dashboard groups events by platform, with one card per platform. Within each card, events sort by urgency (urgent → warn → normal → done) then by deadline. The platform tabs at the top filter to a single platform or show all, with a "隐藏已完成" checkbox to hide `done` events. The `course` field appears in the left column of each row (the sub-classification within the platform, e.g. a course name); this replaces the raw `platform` column since the platform is already the card header.

Codeforces events have `course = ""` (contests are a flat list). Scholat events carry the `course_id` as `course` until fetcher-side course-name resolution is added.

Scholat list page (verified 2026-10): the `td#upload<hwId>` cell holds a status badge — `未提交` / `按时提交` / `延时提交` / `已终止提交`; the deadline cell is `YYYY-MM-DD` plus a policy badge such as `不可延时提交`; the action cell has an `已交` link once submitted (used as a fallback submitted signal). Raw texts are kept in `raw_payload.status_raw` / `policy_raw`.

### Event lifecycle

`status`: `open` (default) → `done` via `POST /events/{id}/done` / `concourse done <id>`.
Fetchers may also emit `submitted` (handed in on the platform), `closed` (platform no longer accepts submissions) or `unknown` (unrecognised raw status — keep the raw text in `raw_payload` so it can be mapped later). The frontend treats `submitted` / `done` / `closed` as finished. A user's `done` is sticky: `upsert_event` won't overwrite it with a fetched `open` / `unknown`, only with another finished status.
`deleted_at`: `null` → soft-deleted via `DELETE /events/{id}` / `concourse delete <id>` → visible only in `GET /trash` / `concourse trash`. Restored via `POST /events/{id}/restore` / `concourse restore <id>`. Permanently removed via `DELETE /events/{id}/permanent` / `concourse delete <id> --permanent` (only on soft-deleted rows).

## Environment variables

```
DATABASE_URL=sqlite+aiosqlite:///./concourse.db
AGENT_TOKEN=<long random secret>
ANTHROPIC_API_KEY=sk-ant-...
```

Copy `.env.example` → `.env`. `shared/config.py` loads them via `pydantic-settings`.
