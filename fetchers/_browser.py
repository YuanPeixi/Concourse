"""Shared Playwright browser utilities for all Playwright-based fetchers."""
from __future__ import annotations

import os

from shared.paths import data_root, platform_directory

BROWSER_DATA_ROOT = data_root() / "browser"


async def launch_context(pw, platform: str, headless: bool = True):
    """Launch a persistent browser context for the given platform.

    Tries msedge → chrome → chromium → bundled-chromium in order.
    Session data is stored under .browser-data/<platform>/ so login state
    persists between runs.
    """
    data_dir = platform_directory("browser", platform)
    data_dir.mkdir(parents=True, exist_ok=True)
    channels = ("chromium",) if os.environ.get("CONCOURSE_DESKTOP") == "1" else ("msedge", "chrome", "chromium", None)
    for channel in channels:
        kwargs = {"channel": channel} if channel else {}
        try:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(data_dir),
                headless=headless,
                accept_downloads=False,
                **kwargs,
            )
            return ctx
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(
        f"无法为 {platform} 启动浏览器（msedge / chrome / chromium 均失败）。"
        "请确认已安装 Playwright 浏览器：uv run playwright install chromium"
    )
