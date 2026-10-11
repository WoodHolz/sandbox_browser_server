"""Create a sandbox, restore browser state, then open Bilibili.

Two ways to drive the browser. Both share create + context restore
(cookies, then the target page, then localStorage / sessionStorage).

    # 用法 1: Playwright runs on this machine, then we notify script-finished.
    python3 examples/bilibili.py usage1

    # 用法 2: this service runs the Playwright script.
    python3 examples/bilibili.py usage2

State file defaults to examples/bilibili_cookies.json.
Service base URL defaults to http://127.0.0.1:8090 (env SANDBOX_BASE).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = os.environ.get("SANDBOX_BASE", "http://127.0.0.1:8090")
COOKIES_PATH = Path(os.environ.get("COOKIES_FILE", Path(__file__).with_name("bilibili_cookies.json")))

# Sent as the /run body. Names cdp_url and browser_id are injected by the service.
REMOTE_SCRIPT = """
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        if "bilibili.com" not in page.url:
            await page.goto("https://www.bilibili.com")
        print(await page.title())
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
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def show(step: str, payload: dict) -> None:
    print(f"\n== {step} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def create_and_inject() -> dict:
    created = request("POST", "/api/v1/sandboxes")
    show("created", created)
    browser_id = created["browser_id"]
    show("status after create", request("GET", f"/api/v1/sandboxes/{browser_id}"))

    state = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
    if isinstance(state, list):
        state = {"url": "https://www.bilibili.com", "cookies": state}
    applied = request("POST", f"/api/v1/sandboxes/{browser_id}/context", state)
    show("context applied", applied)
    show("status after context", request("GET", f"/api/v1/sandboxes/{browser_id}"))
    return created


def usage1() -> None:
    created = create_and_inject()
    browser_id = created["browser_id"]
    os.environ["CDP_URL"] = created["cdp_url"]

    import asyncio
    from playwright.async_api import async_playwright

    async def open_bilibili() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(created["cdp_url"])
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            if "bilibili.com" not in page.url:
                await page.goto("https://www.bilibili.com")
            print("\n== page title ==")
            print(await page.title())
            await browser.close()

    asyncio.run(open_bilibili())
    finished = request("POST", f"/api/v1/sandboxes/{browser_id}/events/script-finished")
    show("script-finished", finished)


def usage2() -> None:
    created = create_and_inject()
    browser_id = created["browser_id"]
    result = request(
        "POST",
        f"/api/v1/sandboxes/{browser_id}/run",
        {"script": REMOTE_SCRIPT},
    )
    show("run finished", result)
    # /run no longer auto-destroys; demo closes explicitly (or use script-finished).
    show("delete", request("DELETE", f"/api/v1/sandboxes/{browser_id}"))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("usage1", "usage2"):
        print("usage: python3 examples/bilibili.py usage1|usage2", file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "usage1":
        usage1()
    else:
        usage2()


if __name__ == "__main__":
    main()
