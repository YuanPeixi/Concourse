"""Build from explicit source allowlists; never copy a developer runtime directory."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from shared.version import __version__


def run(args, env=None):
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def main():
    manifest = ROOT / "package.json"
    package = json.loads(manifest.read_text(encoding="utf-8"))
    if package["version"] != __version__:
        package["version"] = __version__
        manifest.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
        run(["npm.cmd", "install", "--package-lock-only", "--ignore-scripts"])
    # A developer web token must never be compiled into a distributed renderer.
    for name in (".env", ".env.local", ".env.production", ".env.production.local"):
        if (ROOT / "frontend" / name).exists():
            raise SystemExit("Remove frontend build environment files before packaging")
    if any(key.startswith("VITE_") for key in os.environ):
        raise SystemExit("Remove VITE_* build overrides before packaging")
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / "build" / "browsers")
    run([sys.executable, "-m", "playwright", "install", "chromium", "--no-shell"], env)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "desktop/backend.spec"], env)
    run(["npm.cmd", "run", "build", "--prefix", "frontend"])
    run(["npm.cmd", "run", "dist:win"])
    run([sys.executable, "scripts/verify_bundle.py"])


if __name__ == "__main__":
    main()
