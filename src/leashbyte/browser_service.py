"""Private browser-pool service and its authenticated CDP relay."""

import asyncio
import hmac
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .browser import BrowserPool
from .config import Settings
from .errors import LeashByteError
from .models import BrowserActionRequest, BrowserSessionRequest

logger = logging.getLogger("leashbyte.browser")


def allowed_cdp_message(raw: str) -> tuple[bool, dict]:
    try:
        message = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False, {}
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return False, {}
    method = message["method"]
    if method.startswith(("Target.", "Browser.", "Storage.", "Fetch.", "Security.")):
        return False, message
    if method in {
        "Network.setRequestInterception",
        "Network.loadNetworkResource",
        "DOM.setFileInputFiles",
        "Input.dispatchDragEvent",
        "Page.setDownloadBehavior",
    }:
        return False, message
    if method == "Page.navigate":
        params = message.get("params", {})
        if not isinstance(params, dict):
            return False, message
        url = params.get("url", "")
        if not isinstance(url, str) or urlsplit(url).scheme.lower() not in {"http", "https"}:
            return False, message
    return True, message


async def relay_websocket(client: WebSocket, upstream) -> None:
    """Copy text and binary frames in both directions."""

    async def to_upstream() -> None:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                allowed, cdp_message = allowed_cdp_message(message["text"])
                if allowed:
                    await upstream.send(json.dumps(cdp_message))
                else:
                    await client.send_text(
                        json.dumps(
                            {
                                "id": cdp_message.get("id"),
                                "error": {
                                    "code": -32601,
                                    "message": "This CDP method is not available.",
                                },
                            }
                        )
                    )
            elif message.get("bytes") is not None:
                await client.send_text(
                    json.dumps(
                        {
                            "id": None,
                            "error": {"code": -32600, "message": "Use a text CDP message."},
                        }
                    )
                )

    async def to_client() -> None:
        async for message in upstream:
            if isinstance(message, bytes):
                await client.send_bytes(message)
            else:
                await client.send_text(message)

    tasks = [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        with suppress(asyncio.CancelledError):
            await task
    for task in done:
        with suppress(asyncio.CancelledError):
            task.result()


def create_browser_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    service_key = settings.require_service_key()
    pool = BrowserPool(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await pool.start()

        async def expire_sessions() -> None:
            while True:
                await asyncio.sleep(1)
                await pool.reap()

        task = asyncio.create_task(expire_sessions())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await pool.stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error("Browser request error type=%s", type(error).__name__)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "The request failed.",
                        "request_id": request.state.request_id,
                    }
                },
            )
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(LeashByteError)
    async def expected_error(request: Request, error: LeashByteError):
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "The request parameters are invalid.",
                    "request_id": request.state.request_id,
                }
            },
        )

    async def authenticate(x_service_key: Annotated[str | None, Header()] = None) -> None:
        if x_service_key is None or not hmac.compare_digest(
            x_service_key.encode("utf-8"), service_key.encode("utf-8")
        ):
            raise LeashByteError(401, "unauthenticated", "The service credential is not valid.")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "web_bot_auth": pool.signer.enabled, "plugins": bool(pool.plugins)}

    @app.post("/internal/sessions", status_code=201)
    async def create(body: BrowserSessionRequest, _: Annotated[None, Depends(authenticate)]):
        return await pool.create(body.profile, body.start_url, body.proxy, body.lease_seconds)

    @app.get("/internal/sessions/{session_id}")
    async def describe(session_id: str, _: Annotated[None, Depends(authenticate)]):
        return pool.describe(pool.get(session_id))

    @app.delete("/internal/sessions/{session_id}")
    async def close(session_id: str, _: Annotated[None, Depends(authenticate)]):
        await pool.close(session_id)
        return {"id": session_id, "state": "closed", "profile_released": True}

    @app.get("/internal/sessions/{session_id}/snapshot")
    async def snapshot(session_id: str, _: Annotated[None, Depends(authenticate)]):
        return await pool.snapshot(pool.get(session_id))

    @app.post("/internal/sessions/{session_id}/actions")
    async def act(
        session_id: str, body: BrowserActionRequest, _: Annotated[None, Depends(authenticate)]
    ):
        return await pool.act(pool.get(session_id), body)

    @app.get("/internal/sessions/{session_id}/screenshot")
    async def screenshot(session_id: str, _: Annotated[None, Depends(authenticate)]):
        return Response(content=await pool.screenshot(pool.get(session_id)), media_type="image/png")

    @app.get("/internal/sessions/{session_id}/cdp")
    async def cdp(
        session_id: str,
        _: Annotated[None, Depends(authenticate)],
        x_cdp_key: Annotated[str | None, Header()] = None,
    ):
        return await pool.cdp_grant(pool.get(session_id), x_cdp_key)

    @app.websocket("/internal/sessions/{session_id}/cdp/connect")
    async def cdp_connect(websocket: WebSocket, session_id: str):
        supplied_service = websocket.headers.get("X-Service-Key")
        grant = websocket.headers.get("X-CDP-Grant", "")
        if supplied_service is None or not hmac.compare_digest(
            supplied_service.encode("utf-8"), service_key.encode("utf-8")
        ):
            await websocket.close(code=4401)
            return
        try:
            upstream = await pool.open_cdp(session_id, grant)
        except LeashByteError:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        try:
            await relay_websocket(websocket, upstream)
        finally:
            await upstream.close()

    return app
