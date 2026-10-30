"""只读调试：登录 iTEST，打开 exam / 各训练页，把 XHR 响应与页面 HTML 落盘，不写数据库。

uv run python scripts/debug_itestcloud.py
输出到 .debug/itestcloud/<时间戳>/
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fetchers import itestcloud as it  # noqa: E402
from fetchers._browser import launch_context  # noqa: E402

PAGES = {
    "exam": it.EXAM_URL,
    **{f"train-{category}": urljoin(it.BASE, route) for category, route in it.TRAINING_PAGES.items()},
}


async def main() -> None:
    from playwright.async_api import async_playwright

    out = ROOT / ".debug" / it.NAME / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    captured: list[tuple[str, str, str, str]] = []  # (page, method, url, body)
    current = {"name": "login"}

    async with async_playwright() as pw:
        context = await launch_context(pw, it.NAME, headless=True)
        try:
            page = await it.login_with_saved_credentials(context)

            async def on_response(resp):
                if "itestcloud.unipus.cn" not in resp.url:
                    return
                if resp.request.resource_type not in ("xhr", "fetch"):
                    return
                try:
                    body = await resp.text()
                except Exception:  # noqa: BLE001
                    return
                post = resp.request.post_data or ""
                captured.append(
                    (current["name"], resp.request.method, resp.url, f"//REQ {post}\n{body}")
                )

            page.on("response", on_response)
            for name, url in PAGES.items():
                current["name"] = name
                await page.goto(url, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(2000)
                (out / f"{name}.html").write_text(await page.content(), encoding="utf-8")
                text = await page.evaluate("document.body.innerText")
                (out / f"{name}.txt").write_text(text, encoding="utf-8")
                print(f"{name}: final url {page.url}")
                print("  visible text:", text[:400].replace("\n", " | "))
                # 逐个点击可能的筛选标签，触发其他状态的列表请求
                labels = await page.evaluate(
                    """() => [...document.querySelectorAll('li,a,span,div,button')]
                        .filter(e => e.children.length === 0 && e.innerText && e.innerText.trim().length <= 8
                                     && e.offsetParent !== null)
                        .map(e => e.innerText.trim())"""
                )
                seen = set()
                for label in labels:
                    if label in seen or label in ("", "退出"):
                        continue
                    seen.add(label)
                    current["name"] = f"{name}-{label}"
                    try:
                        await page.get_by_text(label, exact=True).first.click(timeout=3000)
                        await page.wait_for_timeout(1500)
                    except Exception:  # noqa: BLE001
                        pass
                    if not page.url.startswith(url):
                        await page.goto(url, wait_until="networkidle", timeout=60000)
                print(f"  clicked labels: {sorted(seen)}")
        finally:
            await context.close()

    index = []
    for i, (name, method, url, body) in enumerate(captured):
        fname = f"{i:02d}_{name}_{re.sub(r'[^A-Za-z0-9]+', '_', url.split('?')[0][-50:])}.json"
        (out / fname).write_text(body, encoding="utf-8")
        index.append({"page": name, "method": method, "url": url, "file": fname, "len": len(body)})
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved to {out}")
    for e in index:
        print(f"  [{e['page']}] {e['method']} {e['url'][:110]} ({e['len']}B)")


if __name__ == "__main__":
    asyncio.run(main())
