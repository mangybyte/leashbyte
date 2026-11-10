"""Essential key-redaction and MCP tool-surface checks."""

import json
import logging

import httpx
import pytest
from leashbyte_sdk import ApiError, CredentialRequest, LeashByteClient
from starlette.testclient import TestClient

from leashbyte.mcp.server import _authenticated_http_app, create_mcp


def test_python_sdk_key_never_appears_in_logs_or_repr(caplog):
    marker = "planted-sdk-key-7f3c91"
    credential_marker = "planted-vault-value-28b61e"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == marker
        return httpx.Response(
            401,
            json={"error": {"code": f"denied-{marker}", "message": f"Bad key: {marker}"}},
            headers={"X-Request-ID": marker},
        )

    client = LeashByteClient(api_key=marker, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO):
        logging.getLogger("leashbyte_sdk").info("Client: %r", client)
        with pytest.raises(ApiError) as caught:
            client.get_config()
    credential = CredentialRequest(
        alias="example",
        username="example-user",
        password=credential_marker,
        allowed_origins=["https://example.invalid"],
    )
    assert marker not in repr(client)
    assert marker not in repr(vars(client))
    assert marker not in repr(client._client.headers)
    assert marker not in caplog.text
    assert marker not in str(caught.value)
    assert marker not in caught.value.code
    assert marker not in str(caught.value.request_id)
    traceback = caught.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get("__name__") == "leashbyte_sdk.client":
            assert marker not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next
    assert credential_marker not in repr(credential)

    sent = {}

    def credential_handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"alias": "example", "allowed_origins": ["https://example.invalid"]},
        )

    with LeashByteClient(
        api_key=marker, transport=httpx.MockTransport(credential_handler)
    ) as wire_client:
        wire_client.create_credential(credential)
    assert sent["password"] == credential_marker


def test_mcp_http_requires_the_same_api_key(monkeypatch):
    monkeypatch.delenv("LEASHBYTE_API_KEY_FILE", raising=False)
    monkeypatch.setenv("LEASHBYTE_API_KEY", "test-mcp-key")
    with TestClient(_authenticated_http_app(create_mcp())) as app:
        for headers in ({}, {"X-API-Key": "wrong-key"}):
            response = app.post("/mcp", json={}, headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "unauthenticated"
        response = app.post("/mcp", json={}, headers={"X-API-Key": "test-mcp-key"})
        assert response.status_code != 401


def test_python_sdk_omits_an_empty_cursor_and_rejects_relative_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "cursor" not in request.url.params
        assert request.url.params["limit"] == "50"
        return httpx.Response(200, json={"runs": [], "next_cursor": None})

    client = LeashByteClient(api_key="test-key", transport=httpx.MockTransport(handler))
    client.list_runs()
    with pytest.raises(ValueError, match="relative path"):
        client.get_run("..")


async def test_mcp_lists_stable_tools_without_vault_or_cdp():
    server = create_mcp()
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert all(tool.is_async for tool in server._tool_manager._tools.values())
    assert {
        "search",
        "fetch",
        "create_browser_session",
        "browser_snapshot",
        "browser_action",
        "create_run",
        "get_run",
        "get_run_steps",
        "cancel_run",
    } <= names
    assert not any("vault" in name or "credential" in name or "cdp" in name for name in names)
