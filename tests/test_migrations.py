import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from backend import migrations
import shared.models  # noqa: F401


def create_legacy(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(engine, tables=[t for n,t in SQLModel.metadata.tables.items() if not n.startswith("reminder_")])
    engine.dispose()
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO fetcher_states (name,enabled,last_error,last_count,last_changed,last_duration_ms) VALUES ('scholat',1,'',0,0,0)")
        db.execute("DROP INDEX ix_events_course")
        db.execute("DROP INDEX ix_events_deleted_at")
        db.execute("DROP INDEX ix_staged_events_course")
        db.execute("ALTER TABLE events DROP COLUMN course")
        db.execute("ALTER TABLE events DROP COLUMN deleted_at")
        db.execute("ALTER TABLE staged_events DROP COLUMN course")
        db.commit()


def run(path, backups):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.begin() as conn:
            migrations.migrate(conn, backups)
    finally:
        engine.dispose()


def test_fresh_and_repeated_start(tmp_path):
    path = tmp_path / "data.db"
    run(path, tmp_path / "backups")
    run(path, tmp_path / "backups")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
    assert not (tmp_path / "backups").exists()


def test_legacy_snapshot_preserves_enabled_and_original_schema(tmp_path):
    path = tmp_path / "legacy.db"
    create_legacy(path)
    run(path, tmp_path / "backups")
    (backup,) = list((tmp_path / "backups").glob("*.sqlite3"))
    with sqlite3.connect(path) as current, sqlite3.connect(backup) as original:
        assert current.execute("SELECT enabled FROM fetcher_states WHERE name='scholat'").fetchone()[0] == 1
        assert "course" in {r[1] for r in current.execute("PRAGMA table_info(events)")}
        assert "course" not in {r[1] for r in original.execute("PRAGMA table_info(events)")}
        assert original.execute("PRAGMA user_version").fetchone()[0] == 0
    run(path, tmp_path / "backups")
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_future_version_refused_without_changes(tmp_path):
    path = tmp_path / "future.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    with pytest.raises(migrations.MigrationError, match="newer"):
        run(path, tmp_path / "backups")
    assert path.read_bytes() == before


def test_unknown_schema_refused(tmp_path):
    path = tmp_path / "foreign.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE foreign_data (id INTEGER)")
    before = path.read_bytes()
    with pytest.raises(migrations.MigrationError, match="Unrecognized"):
        run(path, tmp_path / "backups")
    assert path.read_bytes() == before


def test_migration_failure_rolls_back_schema_and_keeps_backup(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    create_legacy(path)
    def fail(conn):
        conn.exec_driver_sql("ALTER TABLE events ADD COLUMN unexpected TEXT")
        raise RuntimeError("injected failure")
    monkeypatch.setitem(migrations.MIGRATIONS, 1, fail)
    with pytest.raises(migrations.MigrationError, match="Migration failed"):
        run(path, tmp_path / "backups")
    with closing(sqlite3.connect(path)) as db:
        assert "unexpected" not in {r[1] for r in db.execute("PRAGMA table_info(events)")}
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_version_one_upgrade_keeps_backup_and_adds_outbox(tmp_path):
    path = tmp_path / "v1.db"
    create_legacy(path)
    run(path, tmp_path / "first-backups")
    with closing(sqlite3.connect(path)) as db:
        db.execute("DROP TABLE reminder_settings")
        db.execute("DROP TABLE reminder_deliveries")
        db.execute("PRAGMA user_version=1")
        db.commit()
    run(path, tmp_path / "v1-backups")
    assert len(list((tmp_path / "v1-backups").glob("schema-1-*.sqlite3"))) == 1
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM reminder_deliveries").fetchone()[0] == 0
