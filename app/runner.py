"""Run a colleague Playwright script against a sandbox CDP endpoint.

`cdp_url` and `browser_id` are injected as names in the script.
Process exit returns the sandbox to ready; it is NOT torn down. Lifecycle
matches usage 1: heartbeat to keep alive, script-finished or DELETE to close.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import tempfile

from app.manager import manager

logger = logging.getLogger(__name__)


async def run_script(browser_id: str, script: str, timeout_seconds: int = 600) -> dict:
    sandbox = manager.mark_running(browser_id)
    # One file per call, named with this browser_id and the script hash.
    # python -c put the whole source on the command line, which the rotated
    # docker json log then mixed with another container's output.
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()[:12]
    fd, path = tempfile.mkstemp(prefix=f"yuan-{browser_id}-{digest}-", suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                "import os\n"
                "cdp_url = os.environ['CDP_URL']\n"
                "browser_id = os.environ['BROWSER_ID']\n"
            )
            handle.write(script)
            if not script.endswith("\n"):
                handle.write("\n")
        logger.info("run %s script=%s bytes=%s", browser_id, digest, len(script))
        env = dict(os.environ)
        env["CDP_URL"] = sandbox.cdp_url
        env["BROWSER_ID"] = browser_id
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
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
        try:
            os.unlink(path)
        except OSError:
            logger.debug("unlink %s failed", path, exc_info=True)
        # Keep sandbox for the next /run segment; do not auto script-finished.
        if manager.get(browser_id) is not None:
            manager.mark_ready(browser_id)
