"""Writable runtime paths. Importing this module never creates directories."""
from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    override = os.environ.get("CONCOURSE_DATA_DIR")
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            raise ValueError("CONCOURSE_DATA_DIR must be absolute")
        return root.resolve()
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "Concourse" if base else Path.home() / ".concourse").resolve()


def platform_directory(kind: str, platform: str) -> Path:
    if not platform or not all(c.isascii() and (c.isalnum() or c == "_") for c in platform):
        raise ValueError("Invalid platform identifier")
    if kind not in {"credentials", "browser"}:
        raise ValueError("Invalid platform directory")
    return data_root() / kind / platform


def prepare_directories() -> None:
    for name in ("", "credentials", "browser", "logs", "backups"):
        (data_root() / name).mkdir(parents=True, exist_ok=True)
