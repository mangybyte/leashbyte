"""Bounded calls from the public API to private Compose services."""

import httpx

from .errors import LeashByteError


async def internal_request(
    base_url: str,
    service_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> httpx.Response:
    headers = {"X-Service-Key": service_key, **(extra_headers or {})}
    try:
        async with httpx.AsyncClient(
            trust_env=False, timeout=timeout, follow_redirects=False
        ) as client:
            response = await client.request(
                method, f"{base_url}{path}", json=payload, headers=headers
            )
    except httpx.HTTPError as error:
        raise LeashByteError(
            502, "service_unavailable", "An internal service did not answer."
        ) from error
    if response.status_code >= 400:
        try:
            body = response.json()["error"]
            code = body["code"]
            message = body["message"]
            if not isinstance(code, str) or not isinstance(message, str) or len(message) > 300:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            code = "service_request_failed"
            message = "An internal service did not complete the request."
        raise LeashByteError(response.status_code, code, message)
    return response


def response_json(response: httpx.Response):
    try:
        return response.json()
    except ValueError as error:
        raise LeashByteError(
            502, "invalid_service_response", "An internal service returned invalid data."
        ) from error


async def internal_ready(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            response = await client.get(f"{base_url}/healthz")
            return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False
