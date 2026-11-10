"""Versioned SQLite migrations with pre-upgrade snapshot and strict legacy recognition."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlmodel import SQLModel

SCHEMA_VERSION = 2
_LEGACY_ADDITIONS = {
    "events": {"course": "VARCHAR NOT NULL DEFAULT ''", "deleted_at": "DATETIME"},
    "staged_events": {"course": "VARCHAR NOT NULL DEFAULT ''"},
    "fetcher_states": {},
}


class MigrationError(RuntimeError):
    """Permanent startup error; do not retry against the database."""


def _tables(conn):
    return set(conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).scalars())


def _validate(conn, tables, *, legacy, version=SCHEMA_VERSION):
    expected = set(SQLModel.metadata.tables)
    if version < 2:
        expected -= {"reminder_settings", "reminder_deliveries"}
    if not tables <= expected or (tables and not {"events", "staged_events"} <= tables):
        raise MigrationError("Unrecognized database schema; use an untouched supported backup")
    for name in tables:
        rows = list(conn.exec_driver_sql(f'PRAGMA table_info("{name}")'))
        columns = {row[1] for row in rows}
        primary = {row[1] for row in rows if row[5]}
        expected_primary = {column.name for column in SQLModel.metadata.tables[name].primary_key}
        if primary != expected_primary:
            raise MigrationError("Unrecognized database primary key; migration stopped")
        if name == "events":
            unique = []
            for index in conn.exec_driver_sql('PRAGMA index_list("events")'):
                if index[2]:
                    index_name = index[1].replace('"', '""')
                    unique.append(tuple(r[2] for r in conn.exec_driver_sql(f'PRAGMA index_info("{index_name}")')))
            if ("platform", "source_id") not in unique:
                raise MigrationError("Missing event identity constraint; migration stopped")
        wanted = set(SQLModel.metadata.tables[name].columns.keys())
        optional = set(_LEGACY_ADDITIONS.get(name, {})) if legacy else set()
        if not wanted - optional <= columns or not columns <= wanted:
            raise MigrationError("Unrecognized database columns; migration stopped")
    if not legacy and tables != expected:
        raise MigrationError("Incomplete database schema; migration stopped")


def _backup(database: Path, directory: Path, version: int):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = directory / f"schema-{version}-{stamp}-{uuid4().hex[:8]}.sqlite3"
    # Caller holds BEGIN IMMEDIATE: another connection can read the committed snapshot,
    # but no other writer can change it until migration commits or rolls back.
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
        target.commit()
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("Backup integrity check failed; migration stopped")
    return backup


def _to_v1(conn):
    for table in _tables(conn):
        columns = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table}")')}
        for column, definition in _LEGACY_ADDITIONS.get(table, {}).items():
            if column not in columns:
                conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    SQLModel.metadata.create_all(conn, tables=[t for n, t in SQLModel.metadata.tables.items()
                                              if not n.startswith("reminder_")])


def _to_v2(conn):
    SQLModel.metadata.create_all(conn, tables=[t for n, t in SQLModel.metadata.tables.items()
                                              if n.startswith("reminder_")])


MIGRATIONS = {1: _to_v1, 2: _to_v2}


def migrate(conn, backup_directory: Path):
    import shared.models  # noqa: F401

    if conn.dialect.name != "sqlite":
        raise MigrationError("Desktop migrations require SQLite")
    version = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
    if version > SCHEMA_VERSION:
        raise MigrationError("Database is newer than this application; install the newer version")
    tables = _tables(conn)
    _validate(conn, tables, legacy=version == 0, version=version)
    if version == SCHEMA_VERSION:
        return
    conn.exec_driver_sql("BEGIN IMMEDIATE")
    # Re-read under the write lock, so an intervening upgrade cannot be applied twice.
    locked_version = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
    if locked_version != version:
        raise MigrationError("Database changed during startup; restart the application")
    tables = _tables(conn)
    _validate(conn, tables, legacy=version == 0, version=version)
    database = conn.engine.url.database
    if tables:
        if not database or database == ":memory:":
            raise MigrationError("Existing database requires a file-backed snapshot")
        _backup(Path(database), backup_directory, version)
    try:
        for target in range(version + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[target](conn)
            conn.exec_driver_sql(f"PRAGMA user_version={target}")
        _validate(conn, _tables(conn), legacy=False)
    except Exception as exc:
        raise MigrationError("Migration failed; original transaction rolled back; restore pre-upgrade backup if needed") from exc
