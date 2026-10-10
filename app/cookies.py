"""Inject cookies the same way infomator does.

Connect over CDP, pick the current page context (or the browser's first
context), then context.add_cookies before any later navigation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from playwright.async_api import async_playwright

from app.manager import BrowserSandbox

logger = logging.getLogger(__name__)


def normalize_cookie(raw: dict[str, Any]) -> dict[str, Any]:
    cookie: dict[str, Any] = {
        "name": raw["name"],
        "value": raw["value"],
    }
    if raw.get("url"):
        cookie["url"] = raw["url"]
    elif raw.get("domain"):
        cookie["domain"] = raw["domain"]
        cookie["path"] = raw.get("path") or "/"
    else:
        raise ValueError("cookie needs domain or url")
    if "path" in raw and "url" not in cookie:
        cookie["path"] = raw["path"]
    if "expires" in raw and raw["expires"] is not None:
        cookie["expires"] = raw["expires"]
    if "httpOnly" in raw:
        cookie["httpOnly"] = bool(raw["httpOnly"])
    if "secure" in raw:
        cookie["secure"] = bool(raw["secure"])
    same_site = raw.get("sameSite")
    if same_site in ("Strict", "Lax", "None"):
        cookie["sameSite"] = same_site
    return cookie


def _string_map(storage: dict[str, Any] | None) -> dict[str, str]:
    if not storage:
        return {}
    out: dict[str, str] = {}
    for key, value in storage.items():
        out[str(key)] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return out


async def _context(browser):
    for ctx in browser.contexts:
        if ctx.pages:
            return ctx
    if browser.contexts:
        return browser.contexts[0]
    return None


async def apply_context(
    sandbox: BrowserSandbox,
    cookies: list[dict[str, Any]] | None = None,
    url: str | None = None,
    local_storage: dict[str, Any] | None = None,
    session_storage: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Infomator handover order: cookies, then navigate, then storage."""
    payload = [normalize_cookie(item) for item in (cookies or [])]
    local_map = _string_map(local_storage)
    session_map = _string_map(session_storage)
    if not payload and not url and not local_map and not session_map:
        raise ValueError("context is empty")
    if (local_map or session_map) and not url:
        raise ValueError("url is required before localStorage or sessionStorage")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(sandbox.cdp_url)
        context = await _context(browser)
        if context is None:
            await browser.close()
            raise RuntimeError("sandbox browser has no context")
        if payload:
            await context.add_cookies(payload)
        page = context.pages[0] if context.pages else await context.new_page()
        if url:
            await page.goto(url)
        if local_map or session_map:
            await page.evaluate(
                """([localMap, sessionMap]) => {
                    for (const key in localMap) localStorage.setItem(key, localMap[key]);
                    for (const key in sessionMap) sessionStorage.setItem(key, sessionMap[key]);
                }""",
                [local_map, session_map],
            )
        await browser.close()

    if payload:
        sandbox.cookies.extend(payload)
    logger.info(
        "context applied %s cookies url=%s local=%s session=%s",
        len(payload),
        bool(url),
        len(local_map),
        len(session_map),
    )
    return {
        "cookies": len(payload),
        "local_storage": len(local_map),
        "session_storage": len(session_map),
    }


async def inject_cookies(sandbox: BrowserSandbox, cookies: list[dict[str, Any]]) -> int:
    payload = [normalize_cookie(item) for item in cookies]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(sandbox.cdp_url)
        context = None
        for ctx in browser.contexts:
            if ctx.pages:
                context = ctx
                break
        if context is None and browser.contexts:
            context = browser.contexts[0]
        if context is None:
            await browser.close()
            raise RuntimeError("sandbox browser has no context")
        await context.add_cookies(payload)
        await browser.close()
    sandbox.cookies.extend(payload)
    logger.info("injected %s cookies into %s", len(payload), sandbox.browser_id)
    return len(payload)
