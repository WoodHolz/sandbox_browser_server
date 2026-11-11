"""Smoke / lifecycle checks against a deployed sandbox-yuan.

    # Quick path: create → heartbeat → /run own script → heartbeat → DELETE
    SANDBOX_BASE=https://dev.gameday.tech/sandbox python3 examples/lifecycle_test.py smoke

    # No heartbeat: expect destroy around the 30 min create window
    SANDBOX_BASE=https://dev.gameday.tech/sandbox python3 examples/lifecycle_test.py expire

    # Keepalive: heartbeat past the first 30 min window; sandbox must stay up
    SANDBOX_BASE=https://dev.gameday.tech/sandbox python3 examples/lifecycle_test.py keepalive

    # Playwright probe: step prints + optional docker logs (run on the sandbox host)
    SANDBOX_BASE=https://dev.gameday.tech/sandbox python3 examples/lifecycle_test.py playwright

Env (optional):
    SANDBOX_BASE       default http://127.0.0.1:8090
    WINDOW_SECONDS     expire wait, default 1830 (~30m + a little slack)
    KEEP_SECONDS       keepalive duration, default 1900 (past first 30m)
    HEARTBEAT_EVERY    keepalive poll interval, default 600
    POLL_EVERY         expire poll interval, default 30
    EXPECT_REMAIN_MIN  after heartbeat, remain_seconds must be >= this (default 1700)
    TARGET_URL         playwright probe URL (default https://research.ventify.ai)
    KEEP_SANDBOX=1     do not DELETE after playwright probe (inspect docker logs)
    DOCKER_LOG_TAIL    lines of docker logs to print when docker is local (default 80)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("SANDBOX_BASE", "http://127.0.0.1:8090").rstrip("/")
WINDOW_SECONDS = int(os.environ.get("WINDOW_SECONDS", "1830"))
KEEP_SECONDS = int(os.environ.get("KEEP_SECONDS", "1900"))
HEARTBEAT_EVERY = int(os.environ.get("HEARTBEAT_EVERY", "600"))
POLL_EVERY = int(os.environ.get("POLL_EVERY", "30"))
EXPECT_REMAIN_MIN = int(os.environ.get("EXPECT_REMAIN_MIN", "1700"))
TARGET_URL = os.environ.get("TARGET_URL", "https://research.ventify.ai")
KEEP_SANDBOX = os.environ.get("KEEP_SANDBOX", "").strip() in ("1", "true", "yes")
DOCKER_LOG_TAIL = int(os.environ.get("DOCKER_LOG_TAIL", "80"))


class Fail(RuntimeError):
    pass


def request(method: str, path: str, body: dict | None = None, *, timeout: float = 600) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {"http_status": resp.status}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise Fail(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


def request_status(method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            payload = json.loads(raw) if raw else {}
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            payload = json.loads(detail) if detail else {"detail": detail}
        except json.JSONDecodeError:
            payload = {"detail": detail}
        return exc.code, payload


def show(step: str, payload: Any) -> None:
    print(f"\n== {step} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def assert_remain(view: dict, *, label: str) -> None:
    remain = view.get("remain_seconds")
    if remain is None:
        raise Fail(f"{label}: remain_seconds missing")
    if int(remain) < EXPECT_REMAIN_MIN:
        raise Fail(f"{label}: remain_seconds={remain} < EXPECT_REMAIN_MIN={EXPECT_REMAIN_MIN}")


def create() -> dict:
    created = request("POST", "/api/v1/sandboxes")
    show("created", created)
    if created.get("status") != "ready" or not created.get("browser_id"):
        raise Fail(f"create failed: {created}")
    return created


def heartbeat(browser_id: str) -> dict:
    view = request("POST", f"/api/v1/sandboxes/{browser_id}/heartbeat")
    show("heartbeat", view)
    assert_remain(view, label="heartbeat")
    return view


def run_own_script(browser_id: str) -> str:
    marker = f"YUAN_SMOKE_{int(time.time())}_OWN_SCRIPT"
    script = f"""
import asyncio
from playwright.async_api import async_playwright

MARKER = {marker!r}

async def main():
    print(MARKER)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("about:blank")
        print("browser_id=" + browser_id)
        await browser.close()

asyncio.run(main())
"""
    result = request(
        "POST",
        f"/api/v1/sandboxes/{browser_id}/run",
        {"script": script, "timeout_seconds": 120},
    )
    show("run", result)
    if result.get("exit_code") != 0:
        raise Fail(f"/run exit_code={result.get('exit_code')} stderr={result.get('stderr')!r}")
    stdout = result.get("stdout") or ""
    if marker not in stdout:
        raise Fail(f"/run stdout missing own marker {marker!r}: {stdout!r}")
    if f"browser_id={browser_id}" not in stdout:
        raise Fail(f"/run stdout missing browser_id: {stdout!r}")
    if result.get("status") != "ready":
        raise Fail(f"/run left status={result.get('status')!r}, want ready")
    return marker


def destroy(browser_id: str) -> None:
    show("delete", request("DELETE", f"/api/v1/sandboxes/{browser_id}"))
    code, payload = request_status("POST", f"/api/v1/sandboxes/{browser_id}/heartbeat")
    if code != 404:
        raise Fail(f"after DELETE expected heartbeat 404, got {code}: {payload}")
    show("heartbeat after delete", {"http_status": code, **payload})


def print_docker_sandbox_logs(browser_id: str) -> None:
    """Print host docker logs for yuan-sandbox-{id}. Only works on the sandbox host."""
    name = f"yuan-sandbox-{browser_id}"
    docker = shutil.which("docker")
    if not docker:
        print(
            f"\n== docker logs skipped ==\n"
            f"no docker CLI here; on the sandbox host run:\n"
            f"  docker logs --tail {DOCKER_LOG_TAIL} {name}\n"
            f"  docker inspect --format "
            f"'status={{{{.State.Status}}}} oom={{{{.State.OOMKilled}}}} "
            f"exit={{{{.State.ExitCode}}}} log={{{{.HostConfig.LogConfig.Type}}}}' {name}"
        )
        return
    print(f"\n== docker inspect {name} ==")
    inspect = subprocess.run(
        [
            docker,
            "inspect",
            "--format",
            "status={{.State.Status}} oom={{.State.OOMKilled}} "
            "exit={{.State.ExitCode}} finished={{.State.FinishedAt}} "
            "log={{.HostConfig.LogConfig.Type}} "
            "mem={{.HostConfig.Memory}}",
            name,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    print((inspect.stdout or inspect.stderr or "").strip() or f"exit={inspect.returncode}")
    print(f"\n== docker logs --tail {DOCKER_LOG_TAIL} {name} ==")
    logs = subprocess.run(
        [docker, "logs", "--tail", str(DOCKER_LOG_TAIL), name],
        check=False,
        capture_output=True,
        text=True,
    )
    out = (logs.stdout or "") + (logs.stderr or "")
    print(out[-12000:] if out else f"(empty; docker exit={logs.returncode})")


def playwright_probe() -> None:
    """Prove /run actually executes Playwright against the sandbox CDP Chrome."""
    created = create()
    browser_id = created["browser_id"]
    marker = f"PW_PROBE_{int(time.time())}"
    script = f"""
import asyncio
import traceback
from playwright.async_api import async_playwright

MARKER = {marker!r}
TARGET = {TARGET_URL!r}

def step(msg):
    print(f"{{MARKER}} STEP {{msg}}", flush=True)

async def main():
    step("script_enter")
    step("cdp_url=" + repr(cdp_url))
    step("browser_id=" + repr(browser_id))
    try:
        async with async_playwright() as playwright:
            step("playwright_started")
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            step("connected_over_cdp")
            step("contexts=" + str(len(browser.contexts)))
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            step("page_url_before=" + page.url)
            step("goto " + TARGET)
            await page.goto(TARGET, wait_until="domcontentloaded", timeout=120_000)
            step("page_url_after=" + page.url)
            title = await page.title()
            step("title=" + repr(title))
            body = await page.locator("body").inner_text()
            step("body_chars=" + str(len(body)))
            step("has_command_input=" + str(await page.locator("input#command").count()))
            await browser.close()
            step("browser_closed")
        step("script_ok")
    except Exception as exc:
        step("script_fail " + type(exc).__name__ + ": " + str(exc))
        traceback.print_exc()
        raise

asyncio.run(main())
"""
    try:
        show("status before /run", request("GET", f"/api/v1/sandboxes/{browser_id}"))
        print_docker_sandbox_logs(browser_id)
        result = request(
            "POST",
            f"/api/v1/sandboxes/{browser_id}/run",
            {"script": script, "timeout_seconds": 180},
        )
        show("run", result)
        stdout = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        print("\n== /run stdout (raw) ==")
        print(stdout)
        if stderr:
            print("\n== /run stderr (raw) ==")
            print(stderr)
        need = [
            f"{marker} STEP script_enter",
            f"{marker} STEP playwright_started",
            f"{marker} STEP connected_over_cdp",
            f"{marker} STEP goto ",
            f"{marker} STEP script_ok",
        ]
        missing = [line for line in need if line not in stdout]
        if result.get("exit_code") != 0:
            raise Fail(f"playwright /run exit_code={result.get('exit_code')}")
        if missing:
            raise Fail(
                "playwright steps missing from stdout (script may not have run): "
                + ", ".join(missing)
            )
        show("status after /run", request("GET", f"/api/v1/sandboxes/{browser_id}"))
        print_docker_sandbox_logs(browser_id)
        print("\nOK playwright")
    finally:
        if KEEP_SANDBOX:
            print(
                f"\nKEEP_SANDBOX=1: left {browser_id} up. On host:\n"
                f"  docker logs -f yuan-sandbox-{browser_id}\n"
                f"  curl -sS -X DELETE {BASE}/api/v1/sandboxes/{browser_id}"
            )
        else:
            destroy(browser_id)


def smoke() -> None:
    created = create()
    browser_id = created["browser_id"]
    try:
        show("status after create", request("GET", f"/api/v1/sandboxes/{browser_id}"))
        heartbeat(browser_id)
        run_own_script(browser_id)
        show("status after /run", request("GET", f"/api/v1/sandboxes/{browser_id}"))
        heartbeat(browser_id)
    finally:
        destroy(browser_id)
    print("\nOK smoke")


def expire() -> None:
    """No heartbeat after create; sandbox should disappear near MAX_LIFETIME (~30m)."""
    created = create()
    browser_id = created["browser_id"]
    started = time.time()
    deadline = started + WINDOW_SECONDS
    print(
        f"\n== expire watch ==\n"
        f"no heartbeat for up to {WINDOW_SECONDS}s; expect 404 near the create window"
    )
    last_remain = None
    while time.time() < deadline:
        code, payload = request_status("GET", f"/api/v1/sandboxes/{browser_id}")
        elapsed = int(time.time() - started)
        if code == 404:
            print(f"[{elapsed}s] gone (HTTP 404) after ~{elapsed}s without heartbeat")
            show("expire 404", payload)
            if elapsed < 60:
                raise Fail(f"sandbox vanished too early ({elapsed}s); likely not the 30m window")
            print("\nOK expire")
            return
        if code != 200:
            raise Fail(f"GET during expire watch -> HTTP {code}: {payload}")
        last_remain = payload.get("remain_seconds")
        print(f"[{elapsed}s] still {payload.get('status')} remain_seconds={last_remain}")
        time.sleep(POLL_EVERY)

    code, payload = request_status("GET", f"/api/v1/sandboxes/{browser_id}")
    try:
        if code != 404:
            request("DELETE", f"/api/v1/sandboxes/{browser_id}")
    except Fail:
        pass
    raise Fail(
        f"sandbox still present after {WINDOW_SECONDS}s "
        f"(HTTP {code}, remain={last_remain}, payload={payload})"
    )


def keepalive() -> None:
    """Heartbeat past the first create window; must not 404."""
    created = create()
    browser_id = created["browser_id"]
    started = time.time()
    deadline = started + KEEP_SECONDS
    print(
        f"\n== keepalive ==\n"
        f"heartbeat every {HEARTBEAT_EVERY}s for {KEEP_SECONDS}s "
        f"(past the default 30m create window)"
    )
    try:
        while True:
            view = heartbeat(browser_id)
            elapsed = int(time.time() - started)
            print(
                f"[{elapsed}s] alive remain_seconds={view.get('remain_seconds')} "
                f"status={view.get('status')}"
            )
            if time.time() >= deadline:
                break
            sleep_for = min(HEARTBEAT_EVERY, max(1, int(deadline - time.time())))
            time.sleep(sleep_for)
        # One more /run near the end so "own script" still works after >30m wall.
        if KEEP_SECONDS >= 60:
            run_own_script(browser_id)
            heartbeat(browser_id)
        print("\nOK keepalive")
    finally:
        try:
            destroy(browser_id)
        except Fail as exc:
            print(f"cleanup warning: {exc}", file=sys.stderr)


def main() -> None:
    modes = ("smoke", "expire", "keepalive", "playwright")
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        print(
            "usage: python3 examples/lifecycle_test.py "
            + "|".join(modes)
            + f"\nSANDBOX_BASE={BASE}",
            file=sys.stderr,
        )
        sys.exit(2)
    mode = sys.argv[1]
    print(f"SANDBOX_BASE={BASE} mode={mode}")
    if mode == "smoke":
        smoke()
    elif mode == "expire":
        expire()
    elif mode == "keepalive":
        keepalive()
    else:
        playwright_probe()


if __name__ == "__main__":
    try:
        main()
    except Fail as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
