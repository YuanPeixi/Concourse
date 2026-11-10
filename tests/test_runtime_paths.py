import pytest

from shared import config, credentials
from shared.paths import data_root, platform_directory


def test_override_is_absolute_and_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCOURSE_DATA_DIR", str(tmp_path / "隔离 data"))
    assert credentials.path_for("scholat").parent == data_root() / "credentials"
    assert platform_directory("browser", "scholat").parent == data_root() / "browser"
    assert not data_root().exists()
    monkeypatch.setenv("CONCOURSE_DATA_DIR", "relative")
    with pytest.raises(ValueError):
        data_root()


def test_personal_cwd_configuration_never_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONCOURSE_DATA_DIR", str(tmp_path / "isolated"))
    (tmp_path / "config.yaml").write_text("schedule: {scholat: '* * * * *'}")
    (tmp_path / ".env").write_text("AGENT_TOKEN=must-not-load")
    assert config.load_config()["schedule"] == {}
    assert config.Secrets().agent_token == ""


@pytest.mark.parametrize("name", ["../scholat", "a/b", "a\\b", "", "C:", ".."])
def test_platform_cannot_escape_root(name):
    with pytest.raises(ValueError):
        credentials.path_for(name)


def test_future_config_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCOURSE_DATA_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text("schema_version: 999")
    with pytest.raises(ValueError, match="schema"):
        config.load_config()
