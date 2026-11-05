"""配置加载：config.yaml（非敏感）+ .env（敏感）。

敏感项走 pydantic-settings 从环境变量读：
  DATABASE_URL, AGENT_TOKEN, ANTHROPIC_API_KEY
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parent.parent


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./concourse.db"
    agent_token: str = ""
    anthropic_api_key: str = ""
    moodle_calendar_url: SecretStr = SecretStr("")


def load_config() -> dict[str, Any]:
    cfg_path = _ROOT / "config.yaml"
    if cfg_path.exists():
        with cfg_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_config = load_config()
secrets = Secrets()


def fetcher_kwargs(name: str) -> dict[str, Any]:
    return (_config.get("fetchers") or {}).get(name) or {}


def schedule_for(name: str) -> str:
    return (_config.get("schedule") or {}).get(name) or "0 * * * *"


def configured_fetchers() -> list[str]:
    names = set((_config.get("schedule") or {})) | set((_config.get("fetchers") or {}))
    return sorted(names)


def agent_allowed_actions() -> list[str]:
    return (_config.get("agent") or {}).get("allowed_actions") or []


def agent_model() -> str:
    return (_config.get("agent") or {}).get("llm_model") or "[REDACTED]"
