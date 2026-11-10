"""FastAPI application factory."""

import asyncio
import hmac
import logging
import re
import uuid
from typing import Annotated
from urllib.parse import quote

import websockets
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader

from . import __version__
from .browser_service import relay_websocket
from .config import Settings
from .errors import LeashByteError
from .fetch import Fetcher
from .internal import internal_ready, internal_request, response_json
from .models import (
    BrowserActionRequest,
    BrowserCloseResponse,
    BrowserSessionRequest,
    BrowserSessionResponse,
    BrowserSnapshotResponse,
    CdpGrantResponse,
    ConfigResponse,
    CredentialListResponse,
    CredentialMetadata,
    CredentialRequest,
    FetchRequest,
    FetchResponse,
    SearchResponse,
)
from .run_models import (
    CancelRunRequest,
    RunCreateRequest,
    RunListResponse,
    RunResponse,
    StepResponse,
)
from .run_store import ArtifactStore, RunStore
from .run_viewer import viewer_response
from .search import SearXNGBackend

logger = logging.getLogger("leashbyte")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
language_pattern = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?$")


def path_segment(value: str) -> str:
    return quote(value, safe="")


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers={"X-Request-ID": request_id},
    )


def create_app(settings: Settings | None = None, run_store: RunStore | None = None) -> FastAPI:
    settings = settings or Settings()
    api_key = settings.require_api_key()
    search_backend = SearXNGBackend(settings.searxng_base_url, settings.search_timeout_seconds)
    fetcher = Fetcher(settings)
    managed_run_store = run_store

    def runs() -> RunStore:
        nonlocal managed_run_store
        if managed_run_store is None:
            managed_run_store = RunStore(
                settings.database_dsn.get_secret_value(),
                ArtifactStore(settings.run_artifact_root),
            )
        return managed_run_store

    app = FastAPI(
        title="LeashByte",
        version=__version__,
        description="Self-hosted search, page fetch, browser, and vault APIs.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error(
                "Unhandled request error request_id=%s type=%s",
                request.state.request_id,
                type(error).__name__,
            )
            response = error_response(
                request,
                500,
                "internal_error",
                "The request could not be completed.",
            )
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(LeashByteError)
    async def leashbyte_error(request: Request, error: LeashByteError) -> JSONResponse:
        return error_response(request, error.status_code, error.code, error.message)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return error_response(request, error.status_code, "http_error", str(error.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(
            request, 422, "invalid_request", "The request parameters are invalid."
        )

    async def authenticate(provided_key: Annotated[str | None, Depends(api_key_header)]) -> None:
        if provided_key is None or not hmac.compare_digest(
            provided_key.encode("utf-8"), api_key.encode("utf-8")
        ):
            raise LeashByteError(401, "unauthenticated", "Provide a valid X-API-Key header.")

    async def browser(
        method: str, path: str, payload: dict | None = None, extra_headers: dict | None = None
    ):
        response = await internal_request(
            settings.browser_pool_base_url,
            settings.require_service_key(),
            method,
            path,
            payload,
            extra_headers,
            timeout=settings.browser_navigation_timeout_seconds + 30,
        )
        return response_json(response)

    async def vault(method: str, path: str, payload: dict | None = None):
        response = await internal_request(
            settings.vault_base_url,
            settings.require_vault_manage_key(),
            method,
            path,
            payload,
        )
        if response.status_code == 204:
            return None
        return response_json(response)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        checks = await asyncio.gather(
            search_backend.ready(),
            internal_ready(settings.browser_pool_base_url),
            internal_ready(settings.vault_base_url),
            runs().ready(),
        )
        if all(checks):
            return JSONResponse({"status": "ready"})
        return JSONResponse(status_code=503, content={"status": "not_ready"})

    @app.get("/v1/config", response_model=ConfigResponse)
    async def config(_: Annotated[None, Depends(authenticate)]) -> ConfigResponse:
        return ConfigResponse(
            version=__version__,
            features={
                "search": True,
                "fetch_markdown": True,
                "fetch_html": True,
                "fetch_json": True,
                "robots_txt": settings.respect_robots,
                "browser": True,
                "vault": True,
                "cdp": True,
                "plugins": settings.browser_plugins_enabled,
                "agent_runs": True,
            },
            limits={
                "fetch_timeout_seconds": settings.fetch_timeout_seconds,
                "fetch_max_bytes": settings.fetch_max_bytes,
                "fetch_max_redirects": settings.fetch_max_redirects,
                "fetch_max_concurrency": settings.fetch_max_concurrency,
                "search_max_results": settings.search_max_results,
                "browser_max_sessions": settings.browser_max_sessions,
                "browser_lease_seconds": settings.browser_lease_seconds,
                "cdp_grant_seconds": settings.cdp_grant_seconds,
                "agent_max_steps": 100,
                "agent_max_elapsed_seconds": 3600,
                "agent_max_tokens": 100_000,
                "agent_max_navigations": 50,
                "agent_max_artifact_bytes": 20_000_000,
            },
        )

    @app.get("/v1/search", response_model=SearchResponse)
    async def search(
        _: Annotated[None, Depends(authenticate)],
        q: Annotated[str, Query(min_length=1, max_length=500)],
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        language: str = "en",
        safe_search: Annotated[int, Query(ge=0, le=2)] = 1,
    ) -> SearchResponse:
        query = q.strip()
        if not query:
            raise LeashByteError(422, "invalid_query", "The search query must not be empty.")
        if limit > settings.search_max_results:
            raise LeashByteError(
                422,
                "invalid_limit",
                f"The result limit must not exceed {settings.search_max_results}.",
            )
        if not language_pattern.fullmatch(language):
            raise LeashByteError(422, "invalid_language", "The language code is invalid.")
        results = await search_backend.search(query, limit, language.lower(), safe_search)
        return SearchResponse(query=query, results=results)

    @app.post("/v1/fetch", response_model=FetchResponse)
    async def fetch(
        body: FetchRequest,
        _: Annotated[None, Depends(authenticate)],
    ) -> FetchResponse:
        timeout = body.timeout_seconds or settings.fetch_timeout_seconds
        max_bytes = body.max_bytes or settings.fetch_max_bytes
        if timeout > settings.fetch_timeout_seconds:
            raise LeashByteError(
                422,
                "invalid_timeout",
                f"The timeout must not exceed {settings.fetch_timeout_seconds} seconds.",
            )
        if max_bytes > settings.fetch_max_bytes:
            raise LeashByteError(
                422,
                "invalid_byte_limit",
                f"The byte limit must not exceed {settings.fetch_max_bytes}.",
            )
        return await fetcher.fetch(body.url, body.format, timeout, max_bytes)

    @app.post(
        "/v1/browser/sessions",
        status_code=201,
        responses={201: {"model": BrowserSessionResponse}},
    )
    async def create_browser(
        body: BrowserSessionRequest, _: Annotated[None, Depends(authenticate)]
    ):
        return await browser("POST", "/internal/sessions", body.model_dump())

    @app.get(
        "/v1/browser/sessions/{session_id}",
        responses={200: {"model": BrowserSessionResponse}},
    )
    async def describe_browser(session_id: str, _: Annotated[None, Depends(authenticate)]):
        return await browser("GET", f"/internal/sessions/{path_segment(session_id)}")

    @app.delete(
        "/v1/browser/sessions/{session_id}",
        responses={200: {"model": BrowserCloseResponse}},
    )
    async def close_browser(session_id: str, _: Annotated[None, Depends(authenticate)]):
        return await browser("DELETE", f"/internal/sessions/{path_segment(session_id)}")

    @app.get(
        "/v1/browser/sessions/{session_id}/snapshot",
        responses={200: {"model": BrowserSnapshotResponse}},
    )
    async def browser_snapshot(session_id: str, _: Annotated[None, Depends(authenticate)]):
        return await browser("GET", f"/internal/sessions/{path_segment(session_id)}/snapshot")

    @app.post(
        "/v1/browser/sessions/{session_id}/actions",
        responses={200: {"model": BrowserSnapshotResponse}},
    )
    async def browser_action(
        session_id: str, body: BrowserActionRequest, _: Annotated[None, Depends(authenticate)]
    ):
        return await browser(
            "POST",
            f"/internal/sessions/{path_segment(session_id)}/actions",
            body.model_dump(),
        )

    @app.get(
        "/v1/browser/sessions/{session_id}/screenshot",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    async def browser_screenshot(session_id: str, _: Annotated[None, Depends(authenticate)]):
        response = await internal_request(
            settings.browser_pool_base_url,
            settings.require_service_key(),
            "GET",
            f"/internal/sessions/{path_segment(session_id)}/screenshot",
        )
        return Response(content=response.content, media_type="image/png")

    @app.get(
        "/v1/browser/sessions/{session_id}/cdp",
        responses={200: {"model": CdpGrantResponse}},
    )
    async def browser_cdp(
        session_id: str,
        _: Annotated[None, Depends(authenticate)],
        x_cdp_key: Annotated[str | None, Header()] = None,
    ):
        return await browser(
            "GET",
            f"/internal/sessions/{path_segment(session_id)}/cdp",
            extra_headers={"X-CDP-Key": x_cdp_key or ""},
        )

    @app.websocket("/v1/browser/sessions/{session_id}/cdp/connect")
    async def browser_cdp_connect(websocket: WebSocket, session_id: str):
        provided_api_key = websocket.headers.get("X-API-Key", "")
        grant = websocket.headers.get("X-CDP-Grant", "")
        if not hmac.compare_digest(provided_api_key.encode("utf-8"), api_key.encode("utf-8")):
            await websocket.close(code=4401)
            return
        ws_base = settings.browser_pool_base_url.replace("http://", "ws://", 1).replace(
            "https://", "wss://", 1
        )
        url = f"{ws_base}/internal/sessions/{path_segment(session_id)}/cdp/connect"
        try:
            upstream = await websockets.connect(
                url,
                additional_headers={
                    "X-Service-Key": settings.require_service_key(),
                    "X-CDP-Grant": grant,
                },
                max_size=32 * 1024 * 1024,
            )
        except (OSError, websockets.InvalidHandshake):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        try:
            await relay_websocket(websocket, upstream)
        finally:
            await upstream.close()

    @app.post(
        "/v1/vault/credentials",
        status_code=201,
        responses={201: {"model": CredentialMetadata}},
    )
    async def create_credential(body: CredentialRequest, _: Annotated[None, Depends(authenticate)]):
        payload = {
            "alias": body.alias,
            "username": body.username.get_secret_value(),
            "password": body.password.get_secret_value(),
            "allowed_origins": body.allowed_origins,
        }
        return await vault("POST", "/internal/credentials", payload)

    @app.get(
        "/v1/vault/credentials",
        responses={200: {"model": CredentialListResponse}},
    )
    async def list_credentials(_: Annotated[None, Depends(authenticate)]):
        return await vault("GET", "/internal/credentials")

    @app.put(
        "/v1/vault/credentials/{alias}",
        responses={200: {"model": CredentialMetadata}},
    )
    async def replace_credential(
        alias: str, body: CredentialRequest, _: Annotated[None, Depends(authenticate)]
    ):
        if alias != body.alias:
            raise LeashByteError(422, "alias_mismatch", "The path and body aliases must match.")
        payload = {
            "alias": alias,
            "username": body.username.get_secret_value(),
            "password": body.password.get_secret_value(),
            "allowed_origins": body.allowed_origins,
        }
        return await vault("PUT", f"/internal/credentials/{path_segment(alias)}", payload)

    @app.delete("/v1/vault/credentials/{alias}", status_code=204)
    async def delete_credential(alias: str, _: Annotated[None, Depends(authenticate)]):
        await vault("DELETE", f"/internal/credentials/{path_segment(alias)}")
        return Response(status_code=204)

    @app.get("/runs", include_in_schema=False)
    async def run_viewer():
        return viewer_response()

    @app.post(
        "/v1/runs",
        status_code=201,
        responses={201: {"model": RunResponse}},
    )
    async def create_run(body: RunCreateRequest, _: Annotated[None, Depends(authenticate)]):
        model = body.model or (
            "claude-haiku-5-5" if body.provider == "anthropic" else settings.local_model_name
        )
        if not model:
            raise LeashByteError(
                422,
                "model_required",
                "Set LEASHBYTE_LOCAL_MODEL_NAME or provide a model for a local run.",
            )
        store = runs()
        await store.initialize()
        return await store.create(uuid.uuid4().hex, body, model)

    @app.get("/v1/runs", responses={200: {"model": RunListResponse}})
    async def list_runs(
        _: Annotated[None, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ):
        return await runs().list_runs(limit, cursor)

    @app.get("/v1/runs/{run_id}", responses={200: {"model": RunResponse}})
    async def get_run(run_id: str, _: Annotated[None, Depends(authenticate)]):
        return await runs().get(run_id)

    @app.get("/v1/runs/{run_id}/steps", responses={200: {"model": list[StepResponse]}})
    async def get_run_steps(run_id: str, _: Annotated[None, Depends(authenticate)]):
        return await runs().steps(run_id)

    @app.get(
        "/v1/runs/{run_id}/artifacts/{name}",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {},
                    "application/json": {},
                    "image/png": {},
                }
            }
        },
    )
    async def get_run_artifact(run_id: str, name: str, _: Annotated[None, Depends(authenticate)]):
        path, content_type, digest = await runs().artifact(run_id, name)
        return FileResponse(
            path,
            media_type=content_type,
            filename=name,
            headers={"ETag": f'"{digest}"', "Cache-Control": "private, no-store"},
        )

    @app.post("/v1/runs/{run_id}/cancel", responses={200: {"model": RunResponse}})
    async def cancel_run(
        run_id: str,
        body: CancelRunRequest | None = None,
        _: Annotated[None, Depends(authenticate)] = None,
    ):
        reason = body.reason if body is not None else "Cancelled by the user."
        return await runs().cancel(run_id, reason)

    @app.delete("/v1/runs/{run_id}", status_code=204)
    async def delete_run(run_id: str, _: Annotated[None, Depends(authenticate)]):
        await runs().delete(run_id)
        return Response(status_code=204)

    return app
