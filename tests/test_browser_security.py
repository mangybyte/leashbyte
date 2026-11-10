"""Browser isolation, credential, grant and network guard tests."""

import asyncio
import ipaddress
import json
import socket
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from leashbyte.browser import BrowserPool, CDPGrantStore, ProfileLease
from leashbyte.browser_service import allowed_cdp_message
from leashbyte.config import Settings
from leashbyte.errors import LeashByteError
from leashbyte.proxy import GuardedProxy
from leashbyte.url_guard import Target, URLGuard, denied_address
from leashbyte.vault import Vault, exact_https_origin, placeholder_parts
from leashbyte.vault_service import create_vault_app


def settings() -> Settings:
    return Settings(
        api_key="test-only-key",
        respect_robots=False,
        fetch_min_host_interval_seconds=0,
        web_bot_auth_key_file=None,
    )


def test_vault_encrypts_credentials_and_public_views_do_not_leak(tmp_path) -> None:
    secret = "controlled-secret-never-ship-this-value"  # noqa: S105
    path = tmp_path / "credentials.json"
    vault = Vault(path, bytes(range(32)))
    created = vault.put(
        "shop",
        "controlled-user",
        secret,
        ["https://shop.example", "https://shop.example:443"],
    )
    assert created == {"alias": "shop", "allowed_origins": ["https://shop.example"]}
    assert secret not in path.read_text(encoding="utf-8")
    assert "controlled-user" not in path.read_text(encoding="utf-8")
    assert secret not in repr(vault.list())
    assert "controlled-user" not in repr(vault.list())
    assert vault.resolve("shop", "password", "https://shop.example/login") == secret
    with pytest.raises(LeashByteError) as caught:
        vault.resolve("shop", "password", "https://other.example")
    assert caught.value.code == "origin_not_allowed"
    assert secret not in str(caught.value)


def test_origin_and_placeholder_rules_are_exact() -> None:
    assert exact_https_origin("https://shop.example/") == "https://shop.example"
    assert exact_https_origin("https://shop.example:443") == "https://shop.example"
    assert placeholder_parts("{{credential:shop.password}}") == ("shop", "password")
    for origin in [
        "http://shop.example",
        "https://shop.example/path",
        "https://user:pass@shop.example",
        "https://*.example",
    ]:
        with pytest.raises(LeashByteError):
            exact_https_origin(origin)
    with pytest.raises(LeashByteError):
        placeholder_parts("password")


def test_cdp_grant_expires_is_single_use_and_is_session_bound() -> None:
    grants = CDPGrantStore(30)
    token, expires = grants.issue("session-a")
    assert expires == 30
    assert not grants.consume("session-b", token, now=100)
    assert grants.consume("session-a", token, now=100)
    assert not grants.consume("session-a", token, now=100)
    expired, _ = grants.issue("session-a")
    assert not grants.consume("session-a", expired, now=time.time() + 31)


def test_profile_lease_is_exclusive_and_can_be_reacquired(tmp_path) -> None:
    first = ProfileLease(tmp_path, "shop")
    with pytest.raises(LeashByteError) as caught:
        ProfileLease(tmp_path, "shop")
    assert caught.value.code == "profile_in_use"
    first.release()
    second = ProfileLease(tmp_path, "shop")
    second.release()


def test_snapshot_redaction_is_case_insensitive() -> None:
    session = SimpleNamespace(redactions=["User@Leashbyte.com", "two words"])
    assert BrowserPool.redact(session, "USER@leashbyte.COM") == "[redacted]"
    assert BrowserPool.redact(session, "https://shop.example/?password=two+words").endswith(
        "password=[redacted]"
    )


def test_nat64_cannot_hide_a_private_ipv4_destination() -> None:
    assert denied_address(ipaddress.ip_address("64:ff9b::10.0.0.8"))
    assert not denied_address(ipaddress.ip_address("64:ff9b::93.184.216.34"))


def test_example_placeholders_are_not_live_secrets() -> None:
    configured = Settings(api_key="replace-with-a-random-api-key")
    with pytest.raises(RuntimeError, match="Replace the example placeholder"):
        configured.require_api_key()


async def test_browser_guard_denies_navigation_and_subresources(monkeypatch) -> None:
    pool = BrowserPool(settings())
    denied_route = DeniedRoute()
    denied_request = FakeRequest("http://169.254.169.254/latest", False)
    await pool.intercept(None, denied_route, denied_request)
    assert denied_route.aborted == "blockedbyclient"
    assert not denied_route.continued

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )
    allowed_route = DeniedRoute()
    allowed_request = FakeRequest("https://example.com/asset.js", False)
    await pool.intercept(None, allowed_route, allowed_request)
    assert allowed_route.continued
    assert not allowed_route.aborted


class FakeRequest:
    def __init__(self, url: str, navigation: bool) -> None:
        self.url = url
        self.method = "GET"
        self.headers = {}
        self.navigation = navigation

    def is_navigation_request(self) -> bool:
        return self.navigation

    async def all_headers(self) -> dict:
        return self.headers


class DeniedRoute:
    def __init__(self) -> None:
        self.aborted = ""
        self.continued = False

    async def abort(self, reason: str) -> None:
        self.aborted = reason

    async def continue_(self, headers: dict) -> None:
        self.continued = True


async def test_proxy_connects_only_to_a_checked_ip(monkeypatch) -> None:
    attempted = []
    sentinel = (object(), object())

    async def open_connection(host, port):
        attempted.append((host, port))
        return sentinel

    monkeypatch.setattr("leashbyte.proxy.asyncio.open_connection", open_connection)
    target = Target(
        url="https://example.com/",
        host="example.com",
        port=443,
        addresses=(ipaddress.ip_address("93.184.216.34"),),
        allowlisted=False,
    )
    assert await GuardedProxy(URLGuard(set())).connect(target) == sentinel
    assert attempted == [("93.184.216.34", 443)]


async def test_proxy_refuses_a_connect_to_metadata() -> None:
    proxy = GuardedProxy(URLGuard(set()))
    await proxy.start()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"CONNECT 169.254.169.254:80 HTTP/1.1\r\nHost: ignored\r\n\r\n")
        await writer.drain()
        assert await reader.readline() == b"HTTP/1.1 403 Forbidden\r\n"
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        await proxy.stop()


async def test_plain_http_proxy_disables_connection_reuse(monkeypatch) -> None:
    requests = []

    async def upstream_handler(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection : keep-alive\r\n"
            b"Keep-Alive: timeout=60\r\n\r\nok"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )
    proxy = GuardedProxy(URLGuard({("fixture.internal", upstream_port)}))
    await proxy.start()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(
            f"GET http://fixture.internal:{upstream_port}/one HTTP/1.1\r\n"
            "Host: fixture.internal\r\nConnection: keep-alive\r\n"
            "Proxy-Connection: keep-alive\r\n\r\n".encode("ascii")
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=5)
        assert response.endswith(b"\r\n\r\nok")
        assert b"Connection: close\r\n" in response
        assert b"keep-alive" not in response.lower()
        assert b"Connection: close\r\n" in requests[0]
        assert b"keep-alive" not in requests[0].lower()
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        await proxy.stop()
        upstream.close()
        await upstream.wait_closed()


async def test_plain_http_proxy_rejects_bare_lf_response_headers(monkeypatch) -> None:
    async def upstream_handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\nContent-Length: 25\nConnection: keep-alive\n\nok\r\n\r\n")
        await writer.drain()
        await reader.read()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )
    proxy = GuardedProxy(URLGuard({("fixture.internal", upstream_port)}))
    await proxy.start()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(
            f"GET http://fixture.internal:{upstream_port}/one HTTP/1.1\r\n"
            "Host: fixture.internal\r\n\r\n".encode("ascii")
        )
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        await proxy.stop()
        upstream.close()
        await upstream.wait_closed()


async def test_plain_http_proxy_rejects_an_unsolicited_protocol_upgrade(monkeypatch) -> None:
    async def upstream_handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n\r\n"
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok"
        )
        await writer.drain()
        await reader.read()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )
    proxy = GuardedProxy(URLGuard({("fixture.internal", upstream_port)}))
    await proxy.start()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(
            f"GET http://fixture.internal:{upstream_port}/one HTTP/1.1\r\n"
            "Host: fixture.internal\r\n\r\n".encode("ascii")
        )
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        await proxy.stop()
        upstream.close()
        await upstream.wait_closed()


async def test_requested_http_upgrade_still_relays() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
    reader.feed_eof()
    writes = []

    class Writer:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            return None

    await GuardedProxy.relay_http_response(reader, Writer(), allow_upgrade=True)
    assert b"".join(writes).startswith(b"HTTP/1.1 101 Switching Protocols\r\n")


def test_cdp_relay_denies_browser_and_network_escape_methods() -> None:
    denied = [
        "Target.createTarget",
        "Browser.getVersion",
        "Storage.setCookies",
        "Fetch.enable",
        "Security.setIgnoreCertificateErrors",
        "Network.setRequestInterception",
        "Network.loadNetworkResource",
        "DOM.setFileInputFiles",
        "Input.dispatchDragEvent",
        "Page.setDownloadBehavior",
    ]
    for method in denied:
        allowed, _ = allowed_cdp_message(json.dumps({"id": 1, "method": method}))
        assert not allowed
    allowed, _ = allowed_cdp_message(
        json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": "file:///etc/passwd"}})
    )
    assert not allowed
    allowed, _ = allowed_cdp_message(
        json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "1 + 1"}})
    )
    assert allowed


async def test_cdp_and_credential_fills_are_mutually_exclusive() -> None:
    pool = BrowserPool(settings())
    session = SimpleNamespace(id="session", cdp_issued=True, redactions=[], lock=asyncio.Lock())
    with pytest.raises(LeashByteError) as caught:
        await pool.fill_secret(session, object(), "{{credential:shop.password}}")
    assert caught.value.code == "cdp_fill_conflict"

    session.cdp_issued = False
    session.redactions = ["controlled-secret"]
    with pytest.raises(LeashByteError) as caught:
        await pool.cdp_grant(session, "unused")
    assert caught.value.code == "cdp_fill_conflict"


async def test_cdp_grant_waits_for_the_fill_session_lock() -> None:
    pool = BrowserPool(settings())
    session = SimpleNamespace(id="session", cdp_issued=False, redactions=[], lock=asyncio.Lock())
    await session.lock.acquire()
    grant_task = asyncio.create_task(pool.cdp_grant(session, "unused"))
    await asyncio.sleep(0)
    assert not grant_task.done()
    session.redactions.append("controlled-secret")
    session.lock.release()
    with pytest.raises(LeashByteError) as caught:
        await grant_task
    assert caught.value.code == "cdp_fill_conflict"


async def test_screenshot_waits_for_the_fill_session_lock() -> None:
    pool = BrowserPool(settings())

    class Page:
        async def screenshot(self, **kwargs):
            return b"controlled-png"

    session = SimpleNamespace(
        redactions=[],
        lock=asyncio.Lock(),
        context=SimpleNamespace(pages=[Page()]),
    )
    await session.lock.acquire()
    screenshot_task = asyncio.create_task(pool.screenshot(session))
    await asyncio.sleep(0)
    assert not screenshot_task.done()
    session.redactions.append("controlled-secret")
    session.lock.release()
    with pytest.raises(LeashByteError) as caught:
        await screenshot_task
    assert caught.value.code == "screenshot_redaction_active"


async def test_fill_requires_a_main_frame_password_input() -> None:
    pool = BrowserPool(settings())
    session = SimpleNamespace(cdp_issued=False)
    frame = SimpleNamespace(parent_frame=object(), url="https://shop.example/login")

    async def owner_frame():
        return frame

    element = SimpleNamespace(owner_frame=owner_frame)
    with pytest.raises(LeashByteError) as caught:
        await pool.fill_secret(session, element, "{{credential:shop.password}}")
    assert caught.value.code == "invalid_secret_target"

    frame.parent_frame = None

    class TextInput:
        async def owner_frame(self):
            return frame

        async def evaluate(self, script):
            return {"tag": "input", "type": "text"}

    with pytest.raises(LeashByteError) as caught:
        await pool.fill_secret(session, TextInput(), "{{credential:shop.password}}")
    assert caught.value.code == "invalid_secret_target"

    class CrossOriginPasswordInput:
        async def owner_frame(self):
            return frame

        async def evaluate(self, script):
            return {
                "tag": "input",
                "type": "password",
                "form_action": "https://other.example/collect",
            }

    with pytest.raises(LeashByteError) as caught:
        await pool.fill_secret(session, CrossOriginPasswordInput(), "{{credential:shop.password}}")
    assert caught.value.code == "invalid_secret_target"


def test_vault_management_and_resolution_have_separate_credentials(tmp_path) -> None:
    configured = Settings(
        vault_manage_key="management-only",
        vault_resolve_key="resolution-only",
        vault_master_key="00" * 32,
        browser_profile_root=tmp_path / "profiles",
    )
    client = TestClient(create_vault_app(configured))
    payload = {
        "alias": "shop",
        "username": "controlled-user",
        "password": "controlled-password",
        "allowed_origins": ["https://shop.example"],
    }
    denied = client.post(
        "/internal/credentials", json=payload, headers={"X-Service-Key": "browser-only"}
    )
    assert denied.status_code == 401
    created = client.post(
        "/internal/credentials", json=payload, headers={"X-Service-Key": "management-only"}
    )
    assert created.status_code == 201
    denied_resolve = client.post(
        "/internal/resolve",
        json={"alias": "shop", "field": "password", "origin": "https://shop.example"},
        headers={"X-Vault-Resolve-Key": "management-only"},
    )
    assert denied_resolve.status_code == 401
