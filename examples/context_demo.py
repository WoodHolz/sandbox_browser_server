"""Full browser-state restore demo: cookies + localStorage + sessionStorage.

Calls POST /api/v1/sandboxes/{id}/context (same order as infomator), then
reads storage back from the page so colleagues can see injection worked.

    export SANDBOX_BASE=https://dev.gameday.tech/sandbox
    python3 examples/context_demo.py usage2
    python3 examples/context_demo.py usage1   # on the machine that can reach cdp_url

State file: examples/context_demo_state.json (or COOKIES_FILE).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("SANDBOX_BASE", "http://127.0.0.1:8090")
STATE_PATH = Path(os.environ.get("COOKIES_FILE", Path(__file__).with_name("context_demo_state.json")))

# Injected names: cdp_url, browser_id. Verifies storage after /context.
REMOTE_SCRIPT = """
import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        print("page.url=", page.url)
        print("title=", await page.title())
        cookies = await context.cookies()
        print("cookies=", json.dumps(
            [{"name": c["name"], "domain": c.get("domain"), "value": c["value"][:16] + "..."} for c in cookies],
            ensure_ascii=False,
        ))
        storage = await page.evaluate(\"\"\"() => ({
            localStorage: Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])),
            sessionStorage: Object.fromEntries(Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)])),
        })\"\"\")
        print("storage=", json.dumps(storage, ensure_ascii=False))
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
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


def show(step: str, payload: dict) -> None:
    print(f"\n== {step} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def create_and_restore() -> dict:
    created = request("POST", "/api/v1/sandboxes")
    show("created", created)
    browser_id = created["browser_id"]

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if isinstance(state, list):
        raise SystemExit(
            f"{STATE_PATH} must be an object with url/cookies/localStorage/sessionStorage, not a bare list"
        )
    for key in ("url", "cookies", "localStorage", "sessionStorage"):
        if key not in state:
            print(f"warning: state missing {key!r}", file=sys.stderr)

    show("state to apply", {
        "url": state.get("url"),
        "cookies": len(state.get("cookies") or []),
        "localStorage_keys": list((state.get("localStorage") or {}).keys()),
        "sessionStorage_keys": list((state.get("sessionStorage") or {}).keys()),
    })

    applied = request("POST", f"/api/v1/sandboxes/{browser_id}/context", state)
    show("context applied (server verified storage)", applied)
    return created


def usage1() -> None:
    created = create_and_restore()
    browser_id = created["browser_id"]

    import asyncio
    from playwright.async_api import async_playwright

    async def verify() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(created["cdp_url"])
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            print("\n== page ==")
            print(page.url, await page.title())
            storage = await page.evaluate(
                """() => ({
                    localStorage: Object.fromEntries(
                        Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])
                    ),
                    sessionStorage: Object.fromEntries(
                        Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)])
                    ),
                })"""
            )
            show("storage read back", storage)
            await browser.close()

    asyncio.run(verify())
    show("script-finished", request("POST", f"/api/v1/sandboxes/{browser_id}/events/script-finished"))


def usage2() -> None:
    created = create_and_restore()
    browser_id = created["browser_id"]
    result = request(
        "POST",
        f"/api/v1/sandboxes/{browser_id}/run",
        {"script": REMOTE_SCRIPT, "timeout_seconds": 180},
    )
    show("run finished", result)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("usage1", "usage2"):
        print("usage: python3 examples/context_demo.py usage1|usage2", file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "usage1":
        usage1()
    else:
        usage2()


if __name__ == "__main__":
    main()
