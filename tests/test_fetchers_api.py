import os
import tempfile
import time
from pathlib import Path

_DB = Path(tempfile.mkdtemp()) / "test_fetchers.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB.as_posix()}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import scheduler  # noqa: E402
from backend.main import app  # noqa: E402
from shared.models import ScheduleEventCreate  # noqa: E402


class _FakeFetcher:
    name = "codeforces"

    def __init__(self, fail: bool = False, title: str = "Round 1", status: str = "open",
                 raw_payload: dict | None = None):
        self.fail = fail
        self.title = title
        self.status = status
        self.raw_payload = raw_payload or {}

    async def fetch(self, since=None):
        if self.fail:
            raise RuntimeError("boom")
        return [ScheduleEventCreate(
            platform="codeforces", source_id="contest:1", title=self.title, status=self.status,
            raw_payload=self.raw_payload,
        )]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _get(client, name):
    return next(f for f in client.get("/fetchers").json() if f["name"] == name)


def _wait_status(client, name, status):
    for _ in range(100):
        info = _get(client, name)
        if not info["running"] and info["last_status"] == status:
            return info
        time.sleep(0.05)
    raise AssertionError(f"fetcher did not reach {status}")


def test_list_contains_configured(client):
    names = {f["name"] for f in client.get("/fetchers").json()}
    assert {"codeforces", "scholat"} <= names


def test_disable_enable_and_cron_override(client):
    r = client.patch("/fetchers/scholat", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert r.json()["next_run_at"] is None

    r = client.patch("/fetchers/scholat", json={"enabled": True, "cron": "*/30 * * * *"})
    body = r.json()
    assert body["enabled"] is True and body["cron"] == "*/30 * * * *"
    assert body["cron_overridden"] is True and body["next_run_at"] is not None

    r = client.patch("/fetchers/scholat", json={"cron": None})
    assert r.json()["cron"] == r.json()["cron_default"]
    assert r.json()["cron_overridden"] is False


def test_invalid_cron_and_unknown(client):
    assert client.patch("/fetchers/scholat", json={"cron": "not a cron"}).status_code == 422
    assert client.patch("/fetchers/nope", json={"enabled": True}).status_code == 404
    assert client.post("/fetchers/nope/run").status_code == 404


def test_run_now_records_success_and_error(client, monkeypatch):
    monkeypatch.setattr(scheduler, "_load_fetcher", lambda name: _FakeFetcher())
    assert client.post("/fetchers/codeforces/run").status_code == 202
    info = _wait_status(client, "codeforces", "ok")
    assert info["last_count"] == 1
    assert info["last_trigger"] == "manual" and info["last_success_at"]

    monkeypatch.setattr(scheduler, "_load_fetcher", lambda name: _FakeFetcher(fail=True))
    assert client.post("/fetchers/codeforces/run").status_code == 202
    info = _wait_status(client, "codeforces", "error")
    assert "boom" in info["last_error"]
    assert info["last_success_at"]  # 上次成功时间保留


def _run_and_wait(client, name):
    before = _get(client, name)["last_started_at"]
    assert client.post(f"/fetchers/{name}/run").status_code == 202
    for _ in range(100):
        info = _get(client, name)
        if not info["running"] and info["last_started_at"] != before and info["last_finished_at"]:
            return info
        time.sleep(0.05)
    raise AssertionError("fetcher did not finish")


def _cf_event(client):
    return next(e for e in client.get("/events").json() if e["source_id"] == "contest:1")


def test_fetch_does_not_reopen_user_done(client, monkeypatch):
    monkeypatch.setattr(scheduler, "_load_fetcher", lambda name: _FakeFetcher(title="v1"))
    _run_and_wait(client, "codeforces")
    ev = _cf_event(client)
    assert client.post(f"/events/{ev['id']}/done").status_code == 200

    # 平台端内容变了但仍报 open：保留用户的 done
    monkeypatch.setattr(scheduler, "_load_fetcher", lambda name: _FakeFetcher(title="v2"))
    _run_and_wait(client, "codeforces")
    ev = _cf_event(client)
    assert ev["title"] == "v2" and ev["status"] == "done"

    # 平台报 submitted：照常更新
    monkeypatch.setattr(scheduler, "_load_fetcher", lambda name: _FakeFetcher(title="v3", status="submitted"))
    _run_and_wait(client, "codeforces")
    assert _cf_event(client)["status"] == "submitted"


def test_raw_payload_refreshed_without_content_change(client, monkeypatch):
    monkeypatch.setattr(scheduler, "_load_fetcher", lambda name: _FakeFetcher(title="same"))
    _run_and_wait(client, "codeforces")

    # 只有 raw_payload 变了：不算 changed，但已入库的行要拿到新字段
    monkeypatch.setattr(
        scheduler, "_load_fetcher",
        lambda name: _FakeFetcher(title="same", raw_payload={"kind": "contest"}),
    )
    info = _run_and_wait(client, "codeforces")
    assert info["last_changed"] == 0
    assert _cf_event(client)["raw_payload"]["kind"] == "contest"
