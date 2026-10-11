"""Create a sandbox and open research.ventify.ai/auto.html.

Independent of the Bilibili example. No cookies by default.

    # 用法 2（推荐远程 / 本机经反代）
    SANDBOX_BASE=https://dev.gameday.tech/sandbox python3 examples/ventify_auto.py usage2

    # 用法 1（须在能访问 cdp_url 的机器上，一般是开发服务器本机）
    python3 examples/ventify_auto.py usage1

Optional context JSON (cookies / storage) via COOKIES_FILE.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = os.environ.get("SANDBOX_BASE", "http://127.0.0.1:8090")
TARGET_URL = os.environ.get("TARGET_URL", "https://research.ventify.ai/auto.html")
COOKIES_PATH = Path(os.environ.get("COOKIES_FILE", Path(__file__).with_name("ventify_cookies.json")))

REMOTE_SCRIPT = f"""
import asyncio
from playwright.async_api import async_playwright

TARGET = {TARGET_URL!r}

async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        print("navigating to", TARGET)
        try:
            await page.goto(TARGET, wait_until="domcontentloaded", timeout=120_000)
            print("url=", page.url)
            print("title=", await page.title())
        except Exception as exc:
            print("GOTO_FAILED", type(exc).__name__, exc)
            raise
        finally:
            await browser.close()

asyncio.run(main())
"""


def request(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)


def show(step: str, payload: dict) -> None:
    print(f"\n== {step} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def create_sandbox() -> dict:
    created = request("POST", "/api/v1/sandboxes")
    show("created", created)
    browser_id = created["browser_id"]
    show("status after create", request("GET", f"/api/v1/sandboxes/{browser_id}"))

    if COOKIES_PATH.is_file():
        state = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
        if isinstance(state, list):
            state = {"url": TARGET_URL, "cookies": state}
        elif "url" not in state:
            state = {**state, "url": TARGET_URL}
        applied = request("POST", f"/api/v1/sandboxes/{browser_id}/context", state)
        show("context applied", applied)
        show("status after context", request("GET", f"/api/v1/sandboxes/{browser_id}"))
    else:
        print(f"\n== skip context ==\nno {COOKIES_PATH.name}; open page without cookie inject")
    return created


def usage1() -> None:
    created = create_sandbox()
    browser_id = created["browser_id"]

    import asyncio
    from playwright.async_api import async_playwright

    async def open_target() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(created["cdp_url"])
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            print("\n== navigating ==")
            print(TARGET_URL)
            try:
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=120_000)
                print("url=", page.url)
                print("title=", await page.title())
            except Exception as exc:
                print("GOTO_FAILED", type(exc).__name__, exc)
                raise
            finally:
                await browser.close()

    asyncio.run(open_target())
    finished = request("POST", f"/api/v1/sandboxes/{browser_id}/events/script-finished")
    show("script-finished", finished)


def usage2() -> None:
    created = create_sandbox()
    browser_id = created["browser_id"]
    result = request(
        "POST",
        f"/api/v1/sandboxes/{browser_id}/run",
        {"script": REMOTE_SCRIPT, "timeout_seconds": 180},
    )
    show("run finished", result)
    # /run no longer auto-destroys; demo closes explicitly (or use script-finished).
    show("delete", request("DELETE", f"/api/v1/sandboxes/{browser_id}"))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("usage1", "usage2"):
        print("usage: python3 examples/ventify_auto.py usage1|usage2", file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "usage1":
        usage1()
    else:
        usage2()


if __name__ == "__main__":
    main()
