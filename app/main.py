import asyncio
import logging
from contextlib import asynccontextmanager

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.cookies import apply_context, inject_cookies
from app.manager import manager
from app.runner import run_script

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_sweeper())
    yield
    task.cancel()
    for sandbox in manager.list():
        manager.destroy(sandbox.browser_id)


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            manager.sweep()
        except Exception:
            logger.exception("sweep failed")


app = FastAPI(title="sandbox-yuan", lifespan=lifespan)


class CreateResponse(BaseModel):
    browser_id: str
    cdp_url: str
    vnc_url: str
    status: str


class CookieItem(BaseModel):
    name: str
    value: str
    domain: str | None = None
    url: str | None = None
    path: str | None = None
    expires: float | None = None
    httpOnly: bool | None = None
    secure: bool | None = None
    sameSite: str | None = None


class CookiesRequest(BaseModel):
    cookies: list[CookieItem] = Field(min_length=1)


class ContextRequest(BaseModel):
    url: str | None = None
    cookies: list[CookieItem] = Field(default_factory=list)
    localStorage: dict[str, Any] | None = None
    sessionStorage: dict[str, Any] | None = None


class RunRequest(BaseModel):
    script: str = Field(
        description="Python source. Use playwright.async_api and the injected cdp_url."
    )
    timeout_seconds: int = 600


class RunResponse(BaseModel):
    browser_id: str
    status: str
    exit_code: int
    stdout: str
    stderr: str


def _view(sandbox) -> dict:
    return {
        "browser_id": sandbox.browser_id,
        "status": sandbox.status,
        "cdp_url": sandbox.cdp_url,
        "vnc_url": sandbox.vnc_url,
        "created_at": sandbox.created_at,
        "finished_at": sandbox.finished_at,
        "destroy_after": sandbox.destroy_after,
    }


@app.post("/api/v1/sandboxes", response_model=CreateResponse)
async def create_sandbox():
    """Create an infomator sandbox and wait until Chrome CDP answers."""
    try:
        sandbox = await manager.create()
    except Exception as exc:
        logger.exception("create sandbox failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CreateResponse(
        browser_id=sandbox.browser_id,
        cdp_url=sandbox.cdp_url,
        vnc_url=sandbox.vnc_url,
        status=sandbox.status,
    )


@app.get("/api/v1/sandboxes")
def list_sandboxes():
    return [_view(s) for s in manager.list()]


@app.get("/api/v1/sandboxes/{browser_id}")
def get_sandbox(browser_id: str):
    sandbox = manager.get(browser_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    return _view(sandbox)


@app.post("/api/v1/sandboxes/{browser_id}/cookies")
async def upload_cookies(browser_id: str, body: CookiesRequest):
    """Upload cookies and inject them into the sandbox browser context.

    Same step as infomator: connect_over_cdp, then context.add_cookies,
    before the colleague script navigates.
    """
    sandbox = manager.get(browser_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    if sandbox.status == "running":
        raise HTTPException(status_code=409, detail="sandbox is running a script")
    try:
        count = await inject_cookies(sandbox, [item.model_dump(exclude_none=True) for item in body.cookies])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("cookie inject failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"browser_id": browser_id, "injected": count, "stored": len(sandbox.cookies)}


@app.post("/api/v1/sandboxes/{browser_id}/context")
async def upload_context(browser_id: str, body: ContextRequest):
    """Restore browser state in infomator order.

    cookies via context.add_cookies, then goto url, then localStorage
    and sessionStorage on that page.
    """
    sandbox = manager.get(browser_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    if sandbox.status == "running":
        raise HTTPException(status_code=409, detail="sandbox is running a script")
    try:
        applied = await apply_context(
            sandbox,
            cookies=[item.model_dump(exclude_none=True) for item in body.cookies],
            url=body.url,
            local_storage=body.localStorage,
            session_storage=body.sessionStorage,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("context apply failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"browser_id": browser_id, "status": sandbox.status, **applied}


@app.post("/api/v1/sandboxes/{browser_id}/run", response_model=RunResponse)
async def run(browser_id: str, body: RunRequest):
    """Connect Playwright to this sandbox over CDP and run the script."""
    if manager.get(browser_id) is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    try:
        result = await run_script(browser_id, body.script, body.timeout_seconds)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    sandbox = manager.get(browser_id)
    return RunResponse(
        browser_id=browser_id,
        status=sandbox.status if sandbox else "destroyed",
        **result,
    )


@app.post("/api/v1/sandboxes/{browser_id}/events/script-finished")
def script_finished(browser_id: str):
    """Colleague-side Playwright finished. Starts the idle grace timer."""
    try:
        sandbox = manager.script_finished(browser_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="sandbox not found") from None
    return _view(sandbox)


@app.post("/api/v1/sandboxes/{browser_id}/heartbeat")
def heartbeat(browser_id: str):
    """Extend recycle deadline (watchdog for usage 1).

    Each call sets destroy_after to about now + HEARTBEAT_EXTEND_SECONDS,
    capped by created_at + ABSOLUTE_MAX_LIFETIME_SECONDS.
    """
    try:
        sandbox = manager.heartbeat(browser_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="sandbox not found") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(sandbox)


@app.delete("/api/v1/sandboxes/{browser_id}")
def destroy_sandbox(browser_id: str):
    if manager.get(browser_id) is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    manager.destroy(browser_id)
    return {"browser_id": browser_id, "status": "destroyed"}
