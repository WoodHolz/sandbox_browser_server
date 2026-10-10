"""Run a colleague Playwright script against a sandbox CDP endpoint.

`cdp_url` and `browser_id` are injected as names in the script.
Process exit is treated as the explicit script-finished event.
"""

from __future__ import annotations

import asyncio
import os

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
        "python",
        "-c",
        code,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        manager.script_finished(browser_id)
        return {"exit_code": -1, "stdout": "", "stderr": "script timeout"}
    manager.script_finished(browser_id)
    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace")[-8000:],
        "stderr": stderr.decode(errors="replace")[-8000:],
    }
