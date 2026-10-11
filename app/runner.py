"""Run a colleague Playwright script against a sandbox CDP endpoint.

`cdp_url` and `browser_id` are injected as names in the script.
Process exit returns the sandbox to ready; it is NOT torn down. Lifecycle
matches usage 1: heartbeat to keep alive, script-finished or DELETE to close.
"""

from __future__ import annotations

import asyncio
import os
import sys

from app.manager import manager


async def run_script(browser_id: str, script: str, timeout_seconds: int = 600) -> dict:
    sandbox = manager.mark_running(browser_id)
    code = (
        "import os\n"
        "cdp_url = os.environ['CDP_URL']\n"
        "browser_id = os.environ['BROWSER_ID']\n"
        + script
    )
    env = dict(os.environ)
    env["CDP_URL"] = sandbox.cdp_url
    env["BROWSER_ID"] = browser_id
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exit_code": -1, "stdout": "", "stderr": "script timeout"}
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode(errors="replace")[-8000:],
            "stderr": stderr.decode(errors="replace")[-8000:],
        }
    finally:
        # Keep sandbox for the next /run segment; do not auto script-finished.
        if manager.get(browser_id) is not None:
            manager.mark_ready(browser_id)
