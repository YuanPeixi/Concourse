"""Offline legacy import through a disposable staging area; source stays read-only."""
from __future__ import annotations
import hashlib
import json
import sys
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone

import yaml
from sqlalchemy import create_engine

from backend.migrations import migrate
from shared.version import CONFIG_SCHEMA_VERSION
from shared.platforms import REGISTRY


def _copy_stopped_database(source, target):
    if sys.platform != "win32":
        raise ValueError("旧数据库静止验证目前只支持 Windows")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    # Deny all sharing: fails while a legacy service holds the database open.
    handle = kernel.CreateFileW(str(source), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ValueError("旧数据库仍被占用或无法独占读取，请完全退出旧服务")
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    with os.fdopen(descriptor, "rb") as stream, target.open("xb") as output:
        shutil.copyfileobj(stream, output)
    with closing(sqlite3.connect(target)) as snapshot:
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("源数据库完整性检查失败")


def _fingerprint(path: Path):
    if path.is_symlink() or path.is_junction():
        raise ValueError("导入目标包含链接，拒绝自动恢复")
    if path.is_file():
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    if any(p.is_symlink() or p.is_junction() for p in path.rglob("*")):
        raise ValueError("导入副本包含链接")
    return {p.relative_to(path).as_posix(): _fingerprint(p)
            for p in sorted(path.rglob("*")) if p.is_file()}


def _write_journal(destination: Path, value):
    temporary = destination / "import-journal.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination / "import-journal.json")


def recover_import(destination: Path):
    """Rollback an interrupted publication, or finish cleanup after its commit marker."""
    destination = destination.resolve()
    journal = destination / "import-journal.json"
    if not journal.exists():
        return
    value = json.loads(journal.read_text(encoding="utf-8"))
    stage_name = value.get("stage", "")
    if not stage_name.startswith("import-stage-") or Path(stage_name).name != stage_name:
        raise ValueError("导入恢复日志无效，请保留目录并手动恢复")
    stage = destination / stage_name
    if stage.is_symlink() or stage.is_junction():
        raise ValueError("导入恢复目录无效")
    entries = value.get("entries", [])
    validated = []
    for entry in entries:
        relative = Path(entry["path"])
        allowed = relative.as_posix() in {"config.yaml", "browser", "backups", "credentials", "concourse.db"}
        allowed = allowed or (len(relative.parts) == 2 and relative.parts[0] == "credentials"
                              and relative.suffix == ".dpapi" and relative.stem in REGISTRY)
        if not allowed or relative.is_absolute():
            raise ValueError("导入恢复路径无效")
        target = destination / relative
        if not target.resolve().is_relative_to(destination):
            raise ValueError("导入恢复路径越界")
        if target.exists() and _fingerprint(target) != entry["fingerprint"]:
            raise ValueError("导入后文件已被更改，拒绝自动覆盖；请保留目录恢复")
        validated.append((relative, target))
    committed = (destination / "initialized.json").exists()
    if not committed:
        stage.mkdir(exist_ok=True)
        for relative, target in reversed(validated):
            if target.exists():
                recovered = stage / relative
                recovered.parent.mkdir(parents=True, exist_ok=True)
                if recovered.exists():
                    raise ValueError("导入恢复副本冲突，请保留目录")
                os.replace(target, recovered)
    journal.unlink()
    shutil.rmtree(stage, ignore_errors=True)
    return "committed" if committed else "rolled_back"


def eligible(destination: Path):
    return not (destination / "concourse.db").exists() and not (destination / "initialized.json").exists()


def finish(destination: Path, mode: str):
    destination.mkdir(parents=True, exist_ok=True)
    marker = destination / "initialized.json"
    temporary = destination / "initialized.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump({"mode": mode, "created_at": datetime.now(timezone.utc).isoformat()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, marker)


def import_legacy(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    recover_import(destination)
    if not eligible(destination):
        raise ValueError("已有业务数据库或导入完成标记，不能合并或重复导入")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("导入源与目标必须是独立目录")
    database = source / "concourse.db"
    if not database.is_file() or database.is_symlink():
        raise ValueError("旧目录没有可导入数据库")
    # Refuse active WAL sidecars conservatively. Never checkpoint or write the source.
    if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("请先完全关闭旧服务并确认数据库日志已清理")
    destination.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="import-stage-", dir=destination))
    publication = {"stage": stage.name, "entries": []}
    def publish(entry, target):
        if not target.resolve().is_relative_to(destination):
            raise ValueError("导入目标路径越界")
        if target.exists():
            raise ValueError("目标文件已存在，拒绝覆盖")
        publication["entries"].append({"path": target.relative_to(destination).as_posix(),
                                       "fingerprint": _fingerprint(entry)})
        _write_journal(destination, publication)
        os.replace(entry, target)
    try:
        _copy_stopped_database(database, stage / "concourse.db")
        config_file = source / "config.yaml"
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ValueError("旧配置结构无效")
        values = {"schema_version": CONFIG_SCHEMA_VERSION, "schedule": {}, "fetchers": {}}
        for name, platform in REGISTRY.items():
            options = dict((raw.get("fetchers") or {}).get(name) or {})
            options.pop("headless", None)
            # Only supported fields are migrated; credentials never enter config.yaml.
            allowed = platform.options.model_fields
            values["fetchers"][name] = platform.options.model_validate({k:v for k,v in options.items() if k in allowed}).model_dump()
            cron = (raw.get("schedule") or {}).get(name)
            if cron:
                from apscheduler.triggers.cron import CronTrigger
                CronTrigger.from_crontab(cron)
                values["schedule"][name] = cron
        (stage / "config.yaml").write_text(yaml.safe_dump(values, allow_unicode=True), encoding="utf-8")
        engine = create_engine(f"sqlite:///{(stage / 'concourse.db').as_posix()}")
        try:
            with engine.begin() as conn:
                migrate(conn, stage / "backups")
                for name in REGISTRY:
                    conn.exec_driver_sql("INSERT OR IGNORE INTO fetcher_states (name,enabled,last_error,last_count,last_changed,last_duration_ms) VALUES (?,?, '',0,0,0)",
                                         (name, int(name in values["schedule"])))
        finally:
            engine.dispose()
        # Legacy credentials in LocalAppData may already be in destination (case insensitive).
        # Retain them; never overwrite conflicts. Validate decryptability without logging payloads.
        warnings = []
        from shared.credentials import _dpapi_unprotect, _is_windows
        for name in REGISTRY:
            existing = destination / "credentials" / f"{name}.dpapi"
            incoming = source / "credentials" / f"{name}.dpapi"
            if existing.exists() and incoming.exists() and existing.read_bytes() != incoming.read_bytes():
                raise ValueError("凭据存在冲突，请先单独处理后再导入")
            candidate = existing if existing.exists() else incoming
            if candidate.exists():
                try:
                    blob = candidate.read_bytes()
                    json.loads((_dpapi_unprotect(blob) if _is_windows() else blob).decode())
                except Exception:
                    warnings.append(f"{name}: 凭据无法解密，请重新保存")
                    continue
                if not existing.exists():
                    (stage / "credentials").mkdir(exist_ok=True)
                    shutil.copy2(candidate, stage / "credentials" / candidate.name)
        browser = source / ".browser-data"
        if browser.exists():
            try:
                if any(p.is_symlink() or p.is_junction() for p in browser.rglob("*")):
                    raise ValueError("Browser links are not imported")
                shutil.copytree(browser, stage / "browser")
            except Exception:
                warnings.append("浏览器状态未恢复，请使用辅助登录")
                if (stage / "browser").exists():
                    shutil.rmtree(stage / "browser")
        for name in ("config.yaml", "browser", "backups"):
            if (stage / name).exists() and (destination / name).exists():
                raise ValueError("目标已有配置或浏览器数据，拒绝覆盖")
        # Publish database last; startup never sees a half-imported business database.
        for name in ("config.yaml", "browser", "backups", "credentials", "concourse.db"):
            entry = stage / name
            if not entry.exists():
                continue
            if name == "credentials" and (destination / name).exists():
                for file in entry.iterdir():
                    target = destination / name / file.name
                    publish(file, target)
            else:
                target = destination / name
                publish(entry, target)
        finish(destination, "import")
        (destination / "import-journal.json").unlink(missing_ok=True)
        return warnings
    except Exception:
        recover_import(destination)
        raise
    finally:
        if not (destination / "import-journal.json").exists():
            shutil.rmtree(stage, ignore_errors=True)
