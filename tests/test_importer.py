from pathlib import Path
import sqlite3

import pytest

from backend.importer import import_legacy, eligible
from tests.test_migrations import create_legacy


def source(tmp_path):
    root = tmp_path / "旧项目 source"
    root.mkdir()
    create_legacy(root / "concourse.db")
    (root / "config.yaml").write_text("schedule: {codeforces: '0 * * * *'}", encoding="utf-8")
    return root


def test_import_copies_and_preserves_source_and_enabled(tmp_path):
    old = source(tmp_path)
    before = (old / "concourse.db").read_bytes()
    destination = tmp_path / "新 data"
    (destination / "credentials").mkdir(parents=True)
    assert eligible(destination)
    assert import_legacy(old, destination) == []
    assert (old / "concourse.db").read_bytes() == before
    assert not eligible(destination)
    with sqlite3.connect(destination / "concourse.db") as db:
        assert db.execute("SELECT enabled FROM fetcher_states WHERE name='scholat'").fetchone()[0] == 1
        assert db.execute("SELECT enabled FROM fetcher_states WHERE name='codeforces'").fetchone()[0] == 1
    with pytest.raises(ValueError):
        import_legacy(old, destination)


def test_active_source_and_conflicting_destination_refused(tmp_path):
    old = source(tmp_path)
    journal = Path(str(old / "concourse.db") + "-wal")
    journal.write_bytes(b"busy")
    destination = tmp_path / "dest"
    with pytest.raises(ValueError): import_legacy(old, destination)
    journal.unlink()
    destination.mkdir(exist_ok=True)
    (destination / "config.yaml").write_text("keep")
    with pytest.raises(ValueError): import_legacy(old, destination)
    assert (destination / "config.yaml").read_text() == "keep"
    assert not (destination / "concourse.db").exists()
    assert not list(destination.glob("import-stage-*"))


def test_open_source_database_is_refused(tmp_path):
    old = source(tmp_path)
    with (old / "concourse.db").open("rb") as held:
        assert held.read(1)
        with pytest.raises(ValueError, match="占用"):
            import_legacy(old, tmp_path / "destination")


def test_interrupted_publication_recovered_and_retryable(tmp_path, monkeypatch):
    from backend import importer
    old = source(tmp_path)
    destination = tmp_path / "recovery"
    original_finish = importer.finish
    def interrupted(*args):
        raise KeyboardInterrupt("simulated process interruption before commit marker")
    monkeypatch.setattr(importer, "finish", interrupted)
    with pytest.raises(KeyboardInterrupt):
        importer.import_legacy(old, destination)
    assert (destination / "import-journal.json").exists()
    assert (destination / "concourse.db").exists()
    importer.recover_import(destination)
    assert eligible(destination)
    assert not (destination / "config.yaml").exists()
    monkeypatch.setattr(importer, "finish", original_finish)
    importer.import_legacy(old, destination)
    assert (destination / "initialized.json").exists()


def test_recovery_refuses_changed_copy(tmp_path, monkeypatch):
    from backend import importer
    old = source(tmp_path)
    destination = tmp_path / "recovery"
    def interrupted(*args): raise KeyboardInterrupt()
    monkeypatch.setattr(importer, "finish", interrupted)
    with pytest.raises(KeyboardInterrupt): importer.import_legacy(old, destination)
    (destination / "config.yaml").write_text("user changed this")
    with pytest.raises(ValueError, match="已被更改"):
        importer.recover_import(destination)
    assert (destination / "config.yaml").read_text() == "user changed this"
    assert (destination / "import-journal.json").exists()
