import os

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from shared import config, credentials
from shared.platforms import REGISTRY, Options, Platform


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCOURSE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_config", {"schema_version": 1, "schedule": {}, "fetchers": {}})
    with TestClient(app, headers={"Authorization": "Bearer " + os.environ["CONCOURSE_DESKTOP_TOKEN"]}) as client:
        yield client


def test_secret_save_replace_clear_never_echoes(client):
    secret = "private-test-password"
    response = client.put("/fetchers/itestcloud/config", json={"config": {},
        "credentials": {"username": "test-account", "password": secret}})
    assert response.status_code == 200
    assert secret not in response.text and "test-account" not in response.text
    assert response.json()["configured"] and response.json()["credentials_saved"]
    assert response.json()["enabled"] is False
    assert secret not in credentials.path_for("itestcloud").read_text(errors="ignore")
    response = client.put("/fetchers/itestcloud/config", json={"clear_credentials": True})
    assert response.status_code == 200 and not response.json()["configured"]
    assert client.patch("/fetchers/itestcloud", json={"enabled": True}).status_code == 422


def test_invalid_configuration_and_secret_validation_do_not_echo(client):
    response = client.put("/fetchers/scholat/config", json={"config": {"course_ids": ["secret-invalid"]}})
    assert response.status_code == 422 and "secret-invalid" not in response.text
    response = client.put("/fetchers/moodle/config", json={"credentials": {"calendar_url": {"private": "sensitive"}}})
    assert response.status_code == 422 and "sensitive" not in response.text
    assert client.put("/fetchers/codeforces/config", json={"config": {"notify_registered": "false"}}).status_code == 422


def test_registry_adapter_appears_without_scheduler_or_ui_mapping_changes(client, monkeypatch):
    import tests
    import time
    class TestFetcher:
        async def fetch(self):
            return []
    monkeypatch.setattr(tests, "TestFetcher", TestFetcher, raising=False)
    monkeypatch.setitem(REGISTRY, "testadapter", Platform("testadapter", "Test adapter", "#123456", "tests", "TestFetcher", Options))
    response = client.get("/fetchers")
    item = next(x for x in response.json() if x["name"] == "testadapter")
    assert item["description"]["title"] == "Test adapter"
    assert item["enabled"] is False and item["configured"] is True

    assert client.post("/fetchers/testadapter/run").status_code == 202
    for _ in range(100):
        result = next(x for x in client.get("/fetchers").json() if x["name"] == "testadapter")
        if result["last_status"] == "ok":
            break
        time.sleep(.01)
    assert result["last_status"] == "ok"
