"""Private vault service. It has no host port in Compose."""

import hmac
import logging
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .errors import LeashByteError
from .models import CredentialRequest
from .vault import Vault

logger = logging.getLogger("leashbyte.vault")


class ResolveRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=64)
    field: str
    origin: str = Field(min_length=1, max_length=2048)


def create_vault_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    manage_key = settings.require_vault_manage_key()
    resolve_key = settings.require_vault_resolve_key()
    vault = Vault(
        settings.browser_profile_root.parent / "vault" / "credentials.json",
        settings.require_vault_master_key(),
    )
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error("Vault request error type=%s", type(error).__name__)
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

    async def manage_auth(x_service_key: Annotated[str | None, Header()] = None) -> None:
        if x_service_key is None or not hmac.compare_digest(
            x_service_key.encode("utf-8"), manage_key.encode("utf-8")
        ):
            raise LeashByteError(401, "unauthenticated", "The service credential is not valid.")

    async def resolve_auth(x_vault_resolve_key: Annotated[str | None, Header()] = None) -> None:
        if x_vault_resolve_key is None or not hmac.compare_digest(
            x_vault_resolve_key.encode("utf-8"), resolve_key.encode("utf-8")
        ):
            raise LeashByteError(
                401, "unauthenticated", "The vault resolve credential is not valid."
            )

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/internal/credentials", status_code=201)
    async def create(body: CredentialRequest, _: Annotated[None, Depends(manage_auth)]):
        return vault.put(
            body.alias,
            body.username.get_secret_value(),
            body.password.get_secret_value(),
            body.allowed_origins,
        )

    @app.get("/internal/credentials")
    async def list_credentials(_: Annotated[None, Depends(manage_auth)]):
        return {"credentials": vault.list()}

    @app.put("/internal/credentials/{alias}")
    async def replace(
        alias: str, body: CredentialRequest, _: Annotated[None, Depends(manage_auth)]
    ):
        if alias != body.alias:
            raise LeashByteError(422, "alias_mismatch", "The path and body aliases must match.")
        return vault.put(
            alias,
            body.username.get_secret_value(),
            body.password.get_secret_value(),
            body.allowed_origins,
        )

    @app.delete("/internal/credentials/{alias}", status_code=204)
    async def delete(alias: str, _: Annotated[None, Depends(manage_auth)]):
        vault.delete(alias)
        return Response(status_code=204)

    @app.post("/internal/resolve")
    async def resolve(body: ResolveRequest, _: Annotated[None, Depends(resolve_auth)]):
        return {"value": vault.resolve(body.alias, body.field, body.origin)}

    return app
