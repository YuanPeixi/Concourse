"""Desktop child protocol: stdin bootstrap/control, stdout readiness only."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading

from shared.version import __version__


def report(message):
    print(json.dumps(message), flush=True)


def main():
    try:
        from backend.windows_job import contain_process_tree
        contain_process_tree()
        bootstrap = json.loads(sys.stdin.readline(8192))
        token = bootstrap["token"]
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("Invalid bootstrap")
        if bootstrap.get("version") != __version__:
            raise ValueError("Version mismatch")
        os.environ["CONCOURSE_DATA_DIR"] = bootstrap["dataDir"]
        from shared.paths import data_root
        root = data_root()
        for key in ("AGENT_TOKEN", "ANTHROPIC_API_KEY", "MOODLE_CALENDAR_URL"):
            os.environ.pop(key, None)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(root / 'concourse.db').as_posix()}"
        os.environ["CONCOURSE_DESKTOP_TOKEN"] = token
        os.environ["CONCOURSE_DESKTOP"] = "1"
        from backend.importer import recover_import
        if recover_import(root) == "rolled_back":
            report({"type": "fatal", "code": "IMPORT_RECOVERED"})
            return 78
        if bootstrap.get("initialize"):
            from backend.importer import eligible, finish, import_legacy
            from pathlib import Path
            if not eligible(root):
                raise ValueError("Runtime is already initialized")
            source = bootstrap.get("importDir")
            if source:
                warnings = import_legacy(Path(source), root)
                if warnings:
                    report({"type": "import_warning", "codes": warnings})
            else:
                finish(root, "new")
        if bootstrap.get("probeBrowser"):
            async def probe():
                from playwright.async_api import async_playwright
                from fetchers._browser import launch_context
                async with async_playwright() as pw:
                    context = await launch_context(pw, "runtime_probe", headless=True)
                    try:
                        page = await context.new_page()
                        await page.set_content("<html><title>Concourse browser probe</title><p>isolated</p></html>")
                        return await page.title()
                    finally:
                        await context.close()
            title = asyncio.run(probe())
            report({"type": "browser_ready", "title": title})
            return 0
        import uvicorn
        from backend.main import app

        class Server(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started:
                    report({"type": "ready", "port": sock.getsockname()[1], "version": __version__})

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        server = Server(uvicorn.Config(app, log_config=None, access_log=False, timeout_graceful_shutdown=10))

        def controls():
            # EOF also stops the backend when the Electron parent disappears.
            sys.stdin.readline()
            server.should_exit = True

        threading.Thread(target=controls, daemon=True).start()
        try:
            asyncio.run(server.serve(sockets=[sock]))
        finally:
            sock.close()
        if not server.started:
            report({"type": "fatal", "code": "STARTUP_FAILED"})
            return 78
        return 0
    except Exception:
        # Never serialize exception text: configuration may contain secrets.
        report({"type": "fatal", "code": "BOOTSTRAP_FAILED"})
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
