"""配置加载：config.yaml（非敏感）+ .env（敏感）。

敏感项走 pydantic-settings 从环境变量读：
  DATABASE_URL, AGENT_TOKEN, ANTHROPIC_API_KEY
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.paths import data_root
from shared.version import CONFIG_SCHEMA_VERSION

DEFAULT_CONFIG = {"schema_version": CONFIG_SCHEMA_VERSION, "schedule": {}, "fetchers": {}}


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    database_url: str = f"sqlite+aiosqlite:///{(data_root() / 'concourse.db').as_posix()}"
    agent_token: str = ""
    anthropic_api_key: str = ""
    moodle_calendar_url: SecretStr = SecretStr("")


def load_config() -> dict[str, Any]:
    cfg_path = data_root() / "config.yaml"
    if not cfg_path.exists():
        return deepcopy(DEFAULT_CONFIG)
    with cfg_path.open(encoding="utf-8") as f:
        value = yaml.safe_load(f) or {}
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("Unsupported configuration schema; import legacy data explicitly")
    for key in ("schedule", "fetchers"):
        if not isinstance(value.get(key, {}), dict):
            raise ValueError("Invalid configuration structure")
    return value


_config = load_config()
secrets = Secrets()


def fetcher_kwargs(name: str) -> dict[str, Any]:
    return (_config.get("fetchers") or {}).get(name) or {}


def schedule_for(name: str) -> str:
    from shared.platforms import REGISTRY
    return (_config.get("schedule") or {}).get(name) or REGISTRY[name].cron


def configured_fetchers() -> list[str]:
    names = set((_config.get("schedule") or {})) | set((_config.get("fetchers") or {}))
    return sorted(names)


def agent_allowed_actions() -> list[str]:
    return (_config.get("agent") or {}).get("allowed_actions") or []


def agent_model() -> str:
    return (_config.get("agent") or {}).get("llm_model") or "[REDACTED]"


def save_fetcher_config(name: str, options: dict[str, Any]) -> None:
    """Synchronous atomic replacement; secrets must never be passed here."""
    import os
    import tempfile

    global _config
    value = deepcopy(_config)
    value.setdefault("fetchers", {})[name] = options
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(value, stream, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / "config.yaml")
        _config = value
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
