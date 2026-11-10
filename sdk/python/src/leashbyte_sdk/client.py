"""Synchronous Python client for the stable LeashByte API."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, SecretStr

from ._models import (
    BrowserActionRequest,
    BrowserCloseResponse,
    BrowserSessionRequest,
    BrowserSessionResponse,
    BrowserSnapshotResponse,
    CancelRunRequest,
    CdpGrantResponse,
    ConfigResponse,
    CredentialListResponse,
    CredentialMetadata,
    CredentialRequest,
    FetchRequest,
    FetchResponse,
    RunCreateRequest,
    RunListResponse,
    RunResponse,
    SearchResponse,
    StepResponse,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "cancelled", "limit_reached"})
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s,;]+")


def _secret_value(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: _secret_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_secret_value(item) for item in value]
    return value


class LeashByteError(Exception):
    """A safe error that never contains a configured key."""


class ApiError(LeashByteError):
    """An error response from the API."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id


class LeashByteClient:
    """Call the REST API with an API key from an argument or the environment."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        api_key: str | None = None,
        *,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("The base URL must use HTTP or HTTPS and include a host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("The base URL cannot contain credentials, a query or a fragment.")
        key_file = os.environ.get("LEASHBYTE_API_KEY_FILE")
        if api_key is None and key_file:
            api_key = Path(key_file).read_text(encoding="utf-8").strip()
        api_key = api_key or os.environ.get("LEASHBYTE_API_KEY")
        if not api_key:
            raise ValueError("Set api_key, LEASHBYTE_API_KEY or LEASHBYTE_API_KEY_FILE.")
        self.base_url = base_url.rstrip("/")
        secret_key = SecretStr(api_key)
        del api_key
        self._api_key = secret_key
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"LeashByteClient(base_url={self.base_url!r}, api_key='[redacted]')"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LeashByteClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _safe_text(self, value: str, extra_secrets: tuple[str | SecretStr, ...] = ()) -> str:
        result = _BEARER.sub(r"\1[redacted]", value)
        for configured in (self._api_key, *extra_secrets):
            secret = (
                configured.get_secret_value() if isinstance(configured, SecretStr) else configured
            )
            if secret:
                result = result.replace(secret, "[redacted]")
                result = result.replace(quote(secret, safe=""), "[redacted]")
        return result[:2000]

    def _request(
        self,
        method: str,
        path: str,
        *,
        response_type: type[ModelT] | None = None,
        body: BaseModel | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        binary: bool = False,
        extra_secrets: tuple[str | SecretStr, ...] = (),
    ) -> ModelT | bytes | None:
        json_body = _secret_value(body.model_dump(exclude_none=True)) if body else None
        query = {key: value for key, value in (params or {}).items() if value is not None}
        failure = None
        try:
            response = self._client.request(
                method,
                path,
                json=json_body,
                params=query,
                headers={
                    "X-API-Key": self._api_key.get_secret_value(),
                    **(headers or {}),
                },
            )
            response.request.headers.pop("X-API-Key", None)
            response.request.headers.pop("X-CDP-Key", None)
        except httpx.HTTPError as error:
            failure = type(error).__name__
        finally:
            del json_body, headers
        if failure is not None:
            message = self._safe_text(f"The API request failed with {failure}.", extra_secrets)
            raise LeashByteError(message) from None
        if response.status_code >= 400:
            status = response.status_code
            code = "http_error"
            message = f"The API returned HTTP {status}."
            request_id = self._safe_text(response.headers.get("X-Request-ID", ""), extra_secrets)
            payload = None
            error = None
            try:
                payload = response.json()
                error = payload.get("error", {})
                code = self._safe_text(str(error.get("code", code)), extra_secrets)
                message = self._safe_text(str(error.get("message", message)), extra_secrets)
            except (ValueError, AttributeError):
                pass
            finally:
                del response, payload, error
            raise ApiError(status, code, message, request_id or None)
        if binary:
            return response.content
        if response.status_code == 204:
            return None
        payload = None
        try:
            payload = response.json()
            return response_type.model_validate(payload) if response_type else payload
        except (ValueError, TypeError) as error:
            error_type = type(error).__name__
            del response, payload
            raise LeashByteError(
                self._safe_text(
                    f"The API returned an invalid response with {error_type}.",
                    extra_secrets,
                )
            ) from None

    @staticmethod
    def _segment(value: str) -> str:
        if value in {"", ".", ".."}:
            raise ValueError("An identifier cannot be empty or a relative path segment.")
        return quote(value, safe="")

    def health(self) -> dict[str, str]:
        return self._request("GET", "/healthz")

    def ready(self) -> dict[str, str]:
        return self._request("GET", "/readyz")

    def get_config(self) -> ConfigResponse:
        return self._request("GET", "/v1/config", response_type=ConfigResponse)

    def search(
        self, query: str, *, limit: int = 10, language: str = "en", safe_search: int = 1
    ) -> SearchResponse:
        return self._request(
            "GET",
            "/v1/search",
            params={"q": query, "limit": limit, "language": language, "safe_search": safe_search},
            response_type=SearchResponse,
        )

    def fetch(self, request: FetchRequest) -> FetchResponse:
        return self._request("POST", "/v1/fetch", body=request, response_type=FetchResponse)

    def create_browser_session(self, request: BrowserSessionRequest) -> BrowserSessionResponse:
        return self._request(
            "POST", "/v1/browser/sessions", body=request, response_type=BrowserSessionResponse
        )

    def get_browser_session(self, session_id: str) -> BrowserSessionResponse:
        return self._request(
            "GET",
            f"/v1/browser/sessions/{self._segment(session_id)}",
            response_type=BrowserSessionResponse,
        )

    def close_browser_session(self, session_id: str) -> BrowserCloseResponse:
        return self._request(
            "DELETE",
            f"/v1/browser/sessions/{self._segment(session_id)}",
            response_type=BrowserCloseResponse,
        )

    def browser_snapshot(self, session_id: str) -> BrowserSnapshotResponse:
        return self._request(
            "GET",
            f"/v1/browser/sessions/{self._segment(session_id)}/snapshot",
            response_type=BrowserSnapshotResponse,
        )

    def browser_action(
        self, session_id: str, request: BrowserActionRequest
    ) -> BrowserSnapshotResponse:
        return self._request(
            "POST",
            f"/v1/browser/sessions/{self._segment(session_id)}/actions",
            body=request,
            response_type=BrowserSnapshotResponse,
        )

    def browser_screenshot(self, session_id: str) -> bytes:
        return self._request(
            "GET", f"/v1/browser/sessions/{self._segment(session_id)}/screenshot", binary=True
        )

    def cdp_grant(self, session_id: str, cdp_key: str) -> CdpGrantResponse:
        secret = SecretStr(cdp_key)
        del cdp_key
        return self._request(
            "GET",
            f"/v1/browser/sessions/{self._segment(session_id)}/cdp",
            headers={"X-CDP-Key": secret.get_secret_value()},
            response_type=CdpGrantResponse,
            extra_secrets=(secret,),
        )

    def create_credential(self, request: CredentialRequest) -> CredentialMetadata:
        return self._request(
            "POST", "/v1/vault/credentials", body=request, response_type=CredentialMetadata
        )

    def list_credentials(self) -> CredentialListResponse:
        return self._request("GET", "/v1/vault/credentials", response_type=CredentialListResponse)

    def replace_credential(self, request: CredentialRequest) -> CredentialMetadata:
        return self._request(
            "PUT",
            f"/v1/vault/credentials/{self._segment(request.alias)}",
            body=request,
            response_type=CredentialMetadata,
        )

    def delete_credential(self, alias: str) -> None:
        self._request("DELETE", f"/v1/vault/credentials/{self._segment(alias)}")

    def create_run(self, request: RunCreateRequest) -> RunResponse:
        return self._request("POST", "/v1/runs", body=request, response_type=RunResponse)

    def list_runs(self, *, limit: int = 50, cursor: str | None = None) -> RunListResponse:
        return self._request(
            "GET",
            "/v1/runs",
            params={"limit": limit, "cursor": cursor},
            response_type=RunListResponse,
        )

    def get_run(self, run_id: str) -> RunResponse:
        return self._request("GET", f"/v1/runs/{self._segment(run_id)}", response_type=RunResponse)

    def get_run_steps(self, run_id: str) -> list[StepResponse]:
        path = f"/v1/runs/{self._segment(run_id)}/steps"
        payload = self._request("GET", path)
        if not isinstance(payload, list):
            raise LeashByteError("The API returned an invalid run steps response.")
        try:
            return [StepResponse.model_validate(item) for item in payload]
        except (ValueError, TypeError) as error:
            raise LeashByteError(
                f"The API returned invalid run steps with {type(error).__name__}."
            ) from None

    def run_artifact(self, run_id: str, name: str) -> bytes:
        return self._request(
            "GET",
            f"/v1/runs/{self._segment(run_id)}/artifacts/{self._segment(name)}",
            binary=True,
        )

    def cancel_run(self, run_id: str, reason: str = "Cancelled by the user.") -> RunResponse:
        return self._request(
            "POST",
            f"/v1/runs/{self._segment(run_id)}/cancel",
            body=CancelRunRequest(reason=reason),
            response_type=RunResponse,
        )

    def delete_run(self, run_id: str) -> None:
        self._request("DELETE", f"/v1/runs/{self._segment(run_id)}")

    def wait_for_run(
        self, run_id: str, *, timeout_seconds: float = 600, interval_seconds: float = 1
    ) -> RunResponse:
        if timeout_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("The polling timeout and interval must be greater than zero.")
        deadline = time.monotonic() + timeout_seconds
        while True:
            run = self.get_run(run_id)
            if run.state in TERMINAL_RUN_STATES:
                return run
            if time.monotonic() >= deadline:
                raise LeashByteError("The run did not finish before the polling timeout.")
            time.sleep(min(interval_seconds, max(0, deadline - time.monotonic())))
