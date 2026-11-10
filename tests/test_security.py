"""Essential API authentication and untrusted URL guard tests."""

import ipaddress
import socket

import httpcore
import httpx
import pytest
from fastapi.testclient import TestClient
from httpcore._backends.anyio import AnyIOBackend

from leashbyte.config import Settings
from leashbyte.errors import LeashByteError
from leashbyte.fetch import Fetcher, PinnedBackend
from leashbyte.main import create_app
from leashbyte.url_guard import Target, URLGuard


def make_settings(**overrides) -> Settings:
    values = {
        "api_key": "test-only-key",
        "respect_robots": False,
        "fetch_min_host_interval_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


def dns_answer(address: str, port: int = 80):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, port),
        )
    ]


def test_versioned_api_requires_the_configured_key() -> None:
    client = TestClient(create_app(make_settings()))
    denied = client.get("/v1/config")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "unauthenticated"
    assert "request_id" in denied.json()["error"]

    allowed = client.get("/v1/config", headers={"X-API-Key": "test-only-key"})
    assert allowed.status_code == 200
    assert allowed.json()["features"]["fetch_markdown"] is True
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "192.168.1.8",
        "169.254.169.254",
        "100.64.0.8",
        "224.0.0.1",
        "255.255.255.255",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "::ffff:127.0.0.1",
    ],
)
async def test_guard_denies_non_public_addresses(address: str) -> None:
    guard = URLGuard(set())
    with pytest.raises(LeashByteError) as caught:
        await guard.resolve(f"http://[{address}]/" if ":" in address else f"http://{address}/")
    assert caught.value.code == "blocked_address"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("file:///etc/passwd", "invalid_scheme"),
        ("https://user:password@example.com/", "url_credentials"),
        ("http://localhost/", "blocked_host"),
        ("http://service.internal/", "blocked_host"),
        ("http://example.com\\@127.0.0.1/", "invalid_url"),
    ],
)
async def test_guard_rejects_unsafe_url_forms(url: str, code: str) -> None:
    with pytest.raises(LeashByteError) as caught:
        await URLGuard(set()).resolve(url)
    assert caught.value.code == code


async def test_guard_rejects_a_hostname_with_one_private_dns_answer(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: (
            dns_answer("93.184.216.34", port) + dns_answer("10.0.0.8", port)
        ),
    )
    with pytest.raises(LeashByteError) as caught:
        await URLGuard(set()).resolve("https://example.com/")
    assert caught.value.code == "blocked_address"


async def test_guard_has_a_public_address_control(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: dns_answer("93.184.216.34", port),
    )
    target = await URLGuard(set()).resolve("https://example.com/")
    assert str(target.addresses[0]) == "93.184.216.34"
    assert target.port == 443


async def test_guard_permits_a_public_ipv6_literal() -> None:
    target = await URLGuard(set()).resolve("https://[2606:2800::1]/")
    assert str(target.addresses[0]) == "2606:2800::1"
    assert target.host == "2606:2800::1"


async def test_private_allowlist_requires_the_exact_host_and_port(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: dns_answer("127.0.0.1", port),
    )
    guard = URLGuard({("fixture.internal", 8080)})
    target = await guard.resolve("http://fixture.internal:8080/html")
    assert target.allowlisted is True
    with pytest.raises(LeashByteError):
        await guard.resolve("http://fixture.internal:8081/html")


async def test_pinned_backend_uses_checked_ips_and_tries_the_next_address(monkeypatch) -> None:
    backend = PinnedBackend()
    backend.pin(
        Target(
            url="https://example.com/",
            host="example.com",
            port=443,
            addresses=(
                ipaddress.ip_address("93.184.216.34"),
                ipaddress.ip_address("93.184.216.35"),
            ),
            allowlisted=False,
        )
    )
    attempted_hosts = []
    sentinel = object()

    async def connect_tcp(self, host, port, timeout, local_address, socket_options):
        attempted_hosts.append(host)
        if len(attempted_hosts) == 1:
            raise httpcore.ConnectError("Controlled refusal")
        return sentinel

    monkeypatch.setattr(AnyIOBackend, "connect_tcp", connect_tcp)
    assert await backend.connect_tcp("example.com", 443) is sentinel
    assert attempted_hosts == ["93.184.216.34", "93.184.216.35"]


async def test_pinned_backend_rejects_an_unchecked_origin() -> None:
    backend = PinnedBackend()
    backend.pin(
        Target(
            url="https://example.com/",
            host="example.com",
            port=443,
            addresses=(ipaddress.ip_address("93.184.216.34"),),
            allowlisted=False,
        )
    )
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("other.example", 443)


async def test_redirect_destination_is_checked_before_a_second_request(monkeypatch) -> None:
    fetcher = Fetcher(make_settings())
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: dns_answer("93.184.216.34", port),
    )
    calls = 0

    async def redirect(client, backend, target):
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=httpx.Request("GET", target.url),
        )

    monkeypatch.setattr(fetcher, "send", redirect)
    with pytest.raises(LeashByteError) as caught:
        await fetcher.download("https://example.com/start", 5, 1000)
    assert caught.value.code == "blocked_address"
    assert calls == 1


async def test_dns_rebinding_is_denied_on_the_next_connection(monkeypatch) -> None:
    fetcher = Fetcher(make_settings())
    answers = iter(["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: dns_answer(next(answers), port),
    )

    async def success(client, backend, target):
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=httpx.ByteStream(b"ok"),
            request=httpx.Request("GET", target.url),
        )

    monkeypatch.setattr(fetcher, "send", success)
    await fetcher.download("https://example.com/", 5, 1000)
    with pytest.raises(LeashByteError) as caught:
        await fetcher.download("https://example.com/", 5, 1000)
    assert caught.value.code == "blocked_address"


async def test_response_byte_limit_is_enforced() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/plain"},
        stream=httpx.ByteStream(b"12345"),
        request=httpx.Request("GET", "https://example.com/"),
    )
    with pytest.raises(LeashByteError) as caught:
        await Fetcher(make_settings()).read_bounded(response, 4)
    assert caught.value.code == "response_too_large"


async def test_fetch_time_limit_returns_a_bounded_error(monkeypatch) -> None:
    import asyncio

    fetcher = Fetcher(make_settings())
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: dns_answer("93.184.216.34", port),
    )

    async def slow_response(client, backend, target):
        await asyncio.sleep(1)

    monkeypatch.setattr(fetcher, "send", slow_response)
    with pytest.raises(LeashByteError) as caught:
        await fetcher.download("https://example.com/slow", 0.001, 1000)
    assert caught.value.code == "fetch_timeout"
    assert "internal" not in caught.value.message.lower()


async def test_redirect_count_limit_returns_a_bounded_error(monkeypatch) -> None:
    fetcher = Fetcher(make_settings(fetch_max_redirects=0))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family, kind: dns_answer("93.184.216.34", port),
    )

    async def redirect(client, backend, target):
        return httpx.Response(
            302,
            headers={"location": "/next"},
            request=httpx.Request("GET", target.url),
        )

    monkeypatch.setattr(fetcher, "send", redirect)
    with pytest.raises(LeashByteError) as caught:
        await fetcher.download("https://example.com/start", 5, 1000)
    assert caught.value.code == "redirect_limit"
