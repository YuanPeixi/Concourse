"""Inspect the output allowlist and exercise frozen backend/browser in disposable data."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent


def read_line(stream, timeout=45):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(stream.readline()), daemon=True).start()
    return result.get(timeout=timeout)


def main():
    bundle = ROOT / "artifacts" / "win-unpacked"
    backend = bundle / "resources" / "backend" / "concourse-backend.exe"
    browsers = bundle / "resources" / "browsers"
    forbidden = {".env", "config.yaml", "concourse.db", ".browser-data", "credentials"}
    violations = [p.relative_to(bundle).as_posix() for p in bundle.rglob("*")
                  if p.name in forbidden or p.suffix in {".dpapi", ".sqlite3"}]
    if violations:
        raise SystemExit("Forbidden runtime data in bundle: " + str(violations))
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    token = "disposable-frozen-runtime-test-token-12345"
    from shared.version import __version__
    results = {}
    for probe in (False, True):
        data = Path(tempfile.mkdtemp(prefix="concourse-bundle-test-"))
        started = time.perf_counter()
        proc = subprocess.Popen([str(backend)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            proc.stdin.write(json.dumps({"token": token, "dataDir": str(data), "version": __version__, "probeBrowser": probe}) + "\n")
            proc.stdin.flush()
            message = json.loads(read_line(proc.stdout))
            expected = "browser_ready" if probe else "ready"
            if message.get("type") != expected:
                raise RuntimeError("Frozen runtime did not become ready: " + json.dumps(message))
            results["browser_seconds" if probe else "backend_ready_seconds"] = round(time.perf_counter() - started, 3)
            if not probe:
                request = Request(f"http://127.0.0.1:{message['port']}/fetchers", headers={"Authorization": f"Bearer {token}"})
                with urlopen(request, timeout=10) as response:
                    values = json.load(response)
                assert len(values) == 4 and all(not value["enabled"] for value in values)
                proc.stdin.close()
            assert proc.wait(timeout=20) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
    installer = ROOT / "artifacts" / f"Concourse Setup {__version__}.exe"
    results["installer_bytes"] = installer.stat().st_size
    results["sha256"] = hashlib.file_digest(installer.open("rb"), "sha256").hexdigest()
    results["unpacked_bytes"] = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    results["limitations"] = "Developer Windows 11 only; no clean installation, real account, toast delivery or sleep acceptance."
    (ROOT / "artifacts" / "verification.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (ROOT / "artifacts" / "SHA256SUMS.txt").write_text(results["sha256"] + "  " + installer.name + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    main()
