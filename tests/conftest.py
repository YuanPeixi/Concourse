"""Install isolation before importing any application module."""
import os
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="concourse-tests-"))
os.environ["CONCOURSE_DATA_DIR"] = str(_TEST_ROOT)
os.environ["LOCALAPPDATA"] = str(_TEST_ROOT / "local")
os.environ["APPDATA"] = str(_TEST_ROOT / "roaming")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TEST_ROOT / 'test.db').as_posix()}"
for _key in ("AGENT_TOKEN", "ANTHROPIC_API_KEY", "MOODLE_CALENDAR_URL"):
    os.environ.pop(_key, None)

os.environ["CONCOURSE_DESKTOP_TOKEN"] = "isolated-test-token-that-is-not-a-real-secret"
