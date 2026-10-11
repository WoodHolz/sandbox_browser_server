"""Start and stop the infomator sandbox container.

Container contract matches ai-manus DockerSandbox.start:
image, ports 8081 / 9222 / 5901, SERVICE_TIMEOUT_MINUTES, CHROME_ARGS.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

import docker
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

Status = Literal["creating", "ready", "running", "finished", "destroyed"]


@dataclass
class BrowserSandbox:
    browser_id: str
    container_name: str
    status: Status = "creating"
    cdp_url: str = ""
    vnc_url: str = ""
    api_url: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    destroy_after: float | None = None
    cookies: list = field(default_factory=list)


@contextmanager
def docker_client():
    if settings.docker_host:
        client = docker.from_env(environment={"DOCKER_HOST": settings.docker_host})
    else:
        client = docker.from_env()
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:
            logger.debug("docker client close failed", exc_info=True)


class SandboxManager:
    def __init__(self) -> None:
        self._items: dict[str, BrowserSandbox] = {}

    def get(self, browser_id: str) -> BrowserSandbox | None:
        item = self._items.get(browser_id)
        if item is None or item.status == "destroyed":
            return None
        return item

    def list(self) -> list[BrowserSandbox]:
        return [s for s in self._items.values() if s.status != "destroyed"]

    async def create(self) -> BrowserSandbox:
        browser_id = uuid.uuid4().hex[:12]
        name = f"{settings.sandbox_name_prefix}-{browser_id}"
        sandbox = BrowserSandbox(browser_id=browser_id, container_name=name)
        self._items[browser_id] = sandbox
        try:
            self._start_container(sandbox)
            await self._wait_cdp(sandbox)
            sandbox.status = "ready"
            sandbox.destroy_after = time.time() + settings.max_lifetime_seconds
        except Exception:
            self.destroy(browser_id)
            raise
        return sandbox

    def _start_container(self, sandbox: BrowserSandbox) -> None:
        # Container TTL is minutes; API absolute max is seconds — align so heartbeat
        # cannot outlive the image's own SERVICE_TIMEOUT_MINUTES.
        ttl_minutes = max(
            settings.sandbox_ttl_minutes,
            (settings.absolute_max_lifetime_seconds + 59) // 60,
        )
        env = {
            "SERVICE_TIMEOUT_MINUTES": str(ttl_minutes),
            "CHROME_ARGS": settings.sandbox_chrome_args,
        }
        if settings.sandbox_https_proxy:
            env["HTTPS_PROXY"] = settings.sandbox_https_proxy
        if settings.sandbox_http_proxy:
            env["HTTP_PROXY"] = settings.sandbox_http_proxy
        if settings.sandbox_no_proxy:
            env["NO_PROXY"] = settings.sandbox_no_proxy

        api_port = f"{settings.sandbox_api_port}/tcp"
        config = {
            "image": settings.sandbox_image,
            "name": sandbox.container_name,
            "detach": True,
            "auto_remove": True,
            "ports": {api_port: None, "5901/tcp": None, "9222/tcp": None},
            "environment": env,
            "mem_limit": settings.sandbox_mem_limit,
            "cpu_quota": settings.sandbox_cpu_quota,
            "labels": {"app": "sandbox-yuan", "browser_id": sandbox.browser_id},
        }
        if settings.sandbox_network:
            config["network"] = settings.sandbox_network

        with docker_client() as client:
            container = client.containers.run(**config)
            container.reload()
            if settings.sandbox_network:
                net = container.attrs["NetworkSettings"]["Networks"][settings.sandbox_network]
                ip = net["IPAddress"]
                sandbox.api_url = f"http://{ip}:{settings.sandbox_api_port}"
                sandbox.vnc_url = f"ws://{ip}:5901"
                sandbox.cdp_url = f"http://{ip}:9222"
            else:
                ports = container.attrs["NetworkSettings"]["Ports"]
                host = "127.0.0.1"
                sandbox.api_url = f"http://{host}:{ports[api_port][0]['HostPort']}"
                sandbox.vnc_url = f"ws://{host}:{ports['5901/tcp'][0]['HostPort']}"
                sandbox.cdp_url = f"http://{host}:{ports['9222/tcp'][0]['HostPort']}"
        logger.info("sandbox %s cdp %s", sandbox.browser_id, sandbox.cdp_url)

    async def _wait_cdp(self, sandbox: BrowserSandbox) -> None:
        deadline = time.time() + settings.cdp_ready_timeout_seconds
        async with httpx.AsyncClient(timeout=2) as client:
            while time.time() < deadline:
                try:
                    resp = await client.get(f"{sandbox.cdp_url}/json/version")
                    if resp.status_code == 200 and "webSocketDebuggerUrl" in resp.text:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        raise TimeoutError(f"CDP not ready: {sandbox.cdp_url}")

    def mark_running(self, browser_id: str) -> BrowserSandbox:
        sandbox = self._require(browser_id)
        if sandbox.status not in ("ready", "finished"):
            raise RuntimeError(f"sandbox {browser_id} is {sandbox.status}")
        sandbox.status = "running"
        sandbox.finished_at = None
        return sandbox

    def mark_ready(self, browser_id: str) -> BrowserSandbox:
        """Return to ready after /run exits. Does not start idle-grace teardown."""
        sandbox = self._require(browser_id)
        sandbox.status = "ready"
        sandbox.finished_at = None
        return sandbox

    def script_finished(self, browser_id: str) -> BrowserSandbox:
        """Explicit end event. Container stays up for idle_grace_seconds, then is removed."""
        sandbox = self._require(browser_id)
        sandbox.status = "finished"
        sandbox.finished_at = time.time()
        sandbox.destroy_after = sandbox.finished_at + settings.idle_grace_seconds
        return sandbox

    def heartbeat(self, browser_id: str) -> BrowserSandbox:
        """Reset recycle countdown: destroy_after = now + HEARTBEAT_EXTEND_SECONDS.

        Same for usage 1 (CDP) and usage 2 (/run segments). Capped by
        created_at + ABSOLUTE_MAX_LIFETIME_SECONDS. Each call restarts the idle
        timer (countdown form), so remain_seconds jumps back to ~extend.
        """
        sandbox = self._require(browser_id)
        if sandbox.status == "destroyed":
            raise KeyError(browser_id)
        now = time.time()
        ceiling = sandbox.created_at + settings.absolute_max_lifetime_seconds
        if now >= ceiling:
            raise RuntimeError(
                f"sandbox {browser_id} has reached absolute max lifetime "
                f"({settings.absolute_max_lifetime_seconds}s)"
            )
        # Countdown reset (may shorten a longer leftover from create MAX_LIFETIME).
        sandbox.destroy_after = min(now + settings.heartbeat_extend_seconds, ceiling)
        if sandbox.status == "finished":
            sandbox.status = "ready"
            sandbox.finished_at = None
        logger.info(
            "heartbeat %s countdown reset remain=%.0fs destroy_after=%.0f",
            browser_id,
            sandbox.destroy_after - now,
            sandbox.destroy_after,
        )
        return sandbox

    def destroy(self, browser_id: str) -> None:
        sandbox = self._items.get(browser_id)
        if sandbox is None:
            return
        with docker_client() as client:
            try:
                container = client.containers.get(sandbox.container_name)
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
            except Exception:
                logger.exception("remove container %s", sandbox.container_name)
        sandbox.status = "destroyed"
        sandbox.cdp_url = ""

    def tracked_names(self) -> set[str]:
        return {
            s.container_name
            for s in self._items.values()
            if s.status != "destroyed"
        }

    def cleanup_orphans(self) -> int:
        """Remove only this service's orphan containers (never by image name).

        Match rule (both required):
        - Docker label app=sandbox-yuan (set at create; infomator does not use this)
        - Container name starts with "{SANDBOX_NAME_PREFIX}-" (default yuan-sandbox-)

        Infomator sandboxes are typically named sandbox-<id> with compose labels, so
        they are not touched even though they share woodholz/infomator-sandbox.
        """
        tracked = self.tracked_names()
        prefix = f"{settings.sandbox_name_prefix}-"
        # Refuse prefixes that collide with infomator / generic docker sandboxes.
        banned = {"sandbox-", "infomator-sandbox-", "-"}
        if not settings.sandbox_name_prefix or prefix in banned:
            logger.error(
                "refusing orphan cleanup: SANDBOX_NAME_PREFIX=%r is empty or too generic",
                settings.sandbox_name_prefix,
            )
            return 0
        removed = 0
        with docker_client() as client:
            try:
                candidates = client.containers.list(
                    all=True,
                    filters={"label": "app=sandbox-yuan"},
                )
            except Exception:
                logger.exception("list labeled sandboxes failed")
                return 0
            for container in candidates:
                name = container.name or ""
                labels = container.labels or {}
                if labels.get("app") != "sandbox-yuan":
                    continue
                if not name.startswith(prefix):
                    logger.warning(
                        "skip container %s: has app=sandbox-yuan but name not under %s",
                        name,
                        prefix,
                    )
                    continue
                if name in tracked:
                    continue
                try:
                    logger.info("orphan remove %s", name)
                    container.remove(force=True)
                    removed += 1
                except docker.errors.NotFound:
                    pass
                except Exception:
                    logger.exception("orphan remove failed %s", name)
        return removed

    def sweep(self) -> None:
        now = time.time()
        for sandbox in list(self._items.values()):
            if sandbox.status == "destroyed":
                continue
            if sandbox.destroy_after is not None and now >= sandbox.destroy_after:
                if sandbox.status == "running":
                    continue
                logger.info("sweep destroy %s status=%s", sandbox.browser_id, sandbox.status)
                self.destroy(sandbox.browser_id)
        # Containers left after process restart never appear in list(); reap them here.
        try:
            n = self.cleanup_orphans()
            if n:
                logger.info("sweep removed %s orphan sandbox container(s)", n)
        except Exception:
            logger.exception("orphan cleanup failed")

    def _require(self, browser_id: str) -> BrowserSandbox:
        sandbox = self.get(browser_id)
        if sandbox is None:
            raise KeyError(browser_id)
        return sandbox


manager = SandboxManager()
