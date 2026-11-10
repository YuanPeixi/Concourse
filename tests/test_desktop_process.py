"""Exercise real child processes with disposable runtime data, never real accounts."""
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from shared.version import __version__


def line_with_timeout(stream, timeout=15):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(stream.readline()), daemon=True).start()
    return result.get(timeout=timeout)


def launch(tmp_path, version=__version__):
    proc = subprocess.Popen([sys.executable, "-m", "backend.desktop"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    token = "disposable-desktop-integration-token-123456"
    proc.stdin.write(json.dumps({"token": token, "dataDir": str(tmp_path), "version": version}) + "\n")
    proc.stdin.flush()
    return proc, token


def test_handshake_auth_and_graceful_exit(tmp_path):
    proc, token = launch(tmp_path)
    try:
        message = json.loads(line_with_timeout(proc.stdout))
        assert message["type"] == "ready" and message["version"] == __version__
        assert token not in json.dumps(message)
        base = f"http://127.0.0.1:{message['port']}"
        with pytest.raises(HTTPError) as caught:
            urlopen(base + "/events", timeout=3)
        assert caught.value.code == 401
        request = Request(base + "/fetchers", headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=3) as response:
            platforms = json.load(response)
        assert len(platforms) == 4 and all(not p["enabled"] for p in platforms)
        proc.stdin.close()  # Parent EOF must stop even without a shutdown command.
        assert proc.wait(timeout=15) == 0
        assert (tmp_path / "concourse.db").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_mismatched_version_is_permanent_failure(tmp_path):
    proc, _ = launch(tmp_path, version="invalid")
    try:
        assert json.loads(line_with_timeout(proc.stdout))["type"] == "fatal"
        assert proc.wait(timeout=5) == 78
        assert not (tmp_path / "concourse.db").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_forced_backend_exit_kills_descendant():
    code = ("from backend.windows_job import contain_process_tree; contain_process_tree(); "
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'], "
            "creationflags=subprocess.CREATE_NO_WINDOW); print(p.pid,flush=True); time.sleep(120)")
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    handle = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        pid = int(line_with_timeout(parent.stdout))
        handle = kernel.OpenProcess(0x100000, False, pid)
        assert handle
        parent.kill()
        parent.wait(timeout=5)
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if handle:
            kernel.CloseHandle(handle)
