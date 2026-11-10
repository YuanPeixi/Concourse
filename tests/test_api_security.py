import os

from fastapi.testclient import TestClient
from backend.main import app


def test_all_routes_require_authorization():
    with TestClient(app) as client:
        for path in ("/health", "/events", "/staged", "/trash", "/fetchers"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/fetchers/codeforces/run").status_code == 401
        token = os.environ["CONCOURSE_DESKTOP_TOKEN"]
        result = client.get("/health", headers={"Authorization": f"Bearer {token}"})
        assert result.status_code == 200 and result.json()["version"] == "0.1.0"
        assert token not in result.text


def test_untrusted_origin_is_not_allowed():
    with TestClient(app) as client:
        response = client.options("/events", headers={"Origin": "https://untrusted.example",
                                      "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in response.headers
