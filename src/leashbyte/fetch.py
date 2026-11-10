"""Bounded HTTP fetch with DNS pinning, robots checks, and host rate limits."""

import asyncio
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from email.message import Message
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpcore
import httpx
import trafilatura
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream

from .config import Settings
from .errors import LeashByteError
from .models import FetchResponse
from .url_guard import Target, URLGuard

USER_AGENT = "LeashByte/0.1 (+https://leashbyte.com)"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class PinnedBackend(AnyIOBackend):
    """Connect only to addresses checked for the current request origin."""

    def __init__(self) -> None:
        self.target: Target | None = None

    def pin(self, target: Target) -> None:
        self.target = target

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        target = self.target
        if target is None or (host.rstrip(".").lower(), port) != (target.host, target.port):
            raise httpcore.ConnectError("The request origin has no checked addresses.")
        last_error: Exception | None = None
        for address in target.addresses:
            try:
                return await super().connect_tcp(
                    str(address),
                    port,
                    timeout,
                    local_address,
                    socket_options,
                )
            except (OSError, httpcore.NetworkError, httpcore.TimeoutException) as error:
                last_error = error
        raise httpcore.ConnectError("No checked address accepted the connection.") from last_error


@dataclass
class Downloaded:
    source_url: str
    final_url: str
    status: int
    headers: httpx.Headers
    body: bytes


class HostThrottle:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.guard = asyncio.Lock()
        self.locks: dict[str, asyncio.Lock] = {}
        self.last_started: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        async with self.guard:
            lock = self.locks.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self.last_started.get(host, 0)
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self.last_started[host] = time.monotonic()


class Fetcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guard = URLGuard(settings.allowlist())
        self.semaphore = asyncio.Semaphore(settings.fetch_max_concurrency)
        self.throttle = HostThrottle(settings.fetch_min_host_interval_seconds)
        self.robots: dict[str, RobotFileParser | None] = {}
        self.robots_lock = asyncio.Lock()

    async def fetch(
        self, raw_url: str, output_format: str, timeout: float, max_bytes: int
    ) -> FetchResponse:
        try:
            async with asyncio.timeout(timeout):
                async with self.semaphore:
                    if self.settings.respect_robots:
                        await self.check_robots(raw_url, timeout, max_bytes)
                    downloaded = await self.download(raw_url, timeout, max_bytes)
        except TimeoutError as error:
            raise LeashByteError(
                504, "fetch_timeout", "The fetch exceeded its time limit."
            ) from error
        return await self.process(downloaded, output_format)

    async def download(self, raw_url: str, timeout: float, max_bytes: int) -> Downloaded:
        backend = PinnedBackend()
        transport = httpx.AsyncHTTPTransport(
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        # HTTPX has no public network-backend argument. The locked HTTPCore version
        # accepts the backend that pins each request origin to its checked addresses.
        transport._pool = httpcore.AsyncConnectionPool(  # noqa: SLF001
            network_backend=backend,
            max_connections=1,
            max_keepalive_connections=0,
            keepalive_expiry=0,
        )
        client_timeout = httpx.Timeout(
            connect=self.settings.fetch_connect_timeout_seconds,
            read=timeout,
            write=timeout,
            pool=timeout,
        )
        source_url = raw_url
        redirects = 0
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=client_timeout,
                ) as client:
                    current_url = raw_url
                    while True:
                        target = await self.guard.resolve(current_url)
                        await self.throttle.wait(target.host)
                        response = await self.send(client, backend, target)
                        try:
                            if response.status_code in REDIRECT_STATUSES:
                                location = response.headers.get("location")
                                if not location:
                                    raise LeashByteError(
                                        502,
                                        "invalid_redirect",
                                        f"The host {target.host} returned an invalid redirect.",
                                    )
                                if redirects >= self.settings.fetch_max_redirects:
                                    raise LeashByteError(
                                        502,
                                        "redirect_limit",
                                        "The fetch exceeded its redirect limit.",
                                    )
                                current_url = urljoin(target.url, location)
                                redirects += 1
                                continue
                            body = await self.read_bounded(response, max_bytes)
                            return Downloaded(
                                source_url=source_url,
                                final_url=target.url,
                                status=response.status_code,
                                headers=response.headers,
                                body=body,
                            )
                        finally:
                            await response.aclose()
        except TimeoutError as error:
            raise LeashByteError(
                504, "fetch_timeout", "The fetch exceeded its time limit."
            ) from error
        except httpx.HTTPError as error:
            raise LeashByteError(
                502,
                "fetch_failed",
                "The destination host did not complete the request.",
            ) from error

    async def send(
        self,
        client: httpx.AsyncClient,
        backend: PinnedBackend,
        target: Target,
    ) -> httpx.Response:
        backend.pin(target)
        request = client.build_request(
            "GET",
            target.url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        return await client.send(request, stream=True)

    async def read_bounded(self, response: httpx.Response, max_bytes: int) -> bytes:
        encoding = response.headers.get("content-encoding", "identity").lower().strip()
        if encoding not in {"", "identity"}:
            raise LeashByteError(
                502,
                "unsupported_encoding",
                "The destination did not honor the identity content encoding request.",
            )
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_bytes:
            raise LeashByteError(413, "response_too_large", "The response exceeds its byte limit.")
        body = bytearray()
        compressed_bytes = 0
        decompressed_bytes = 0
        async for chunk in response.aiter_raw():
            compressed_bytes += len(chunk)
            decompressed_bytes += len(chunk)
            if compressed_bytes > max_bytes or decompressed_bytes > max_bytes:
                raise LeashByteError(
                    413,
                    "response_too_large",
                    "The response exceeds its byte limit.",
                )
            body.extend(chunk)
        return bytes(body)

    async def check_robots(self, raw_url: str, timeout: float, max_bytes: int) -> None:
        target = await self.guard.resolve(raw_url)
        parts = urlsplit(target.url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self.robots_lock:
            if origin in self.robots:
                parser = self.robots[origin]
            else:
                parser = None
                robots_url = f"{origin}/robots.txt"
                try:
                    downloaded = await self.download(
                        robots_url,
                        timeout,
                        min(max_bytes, 512_000),
                    )
                except LeashByteError as error:
                    if error.code in {"blocked_address", "blocked_host", "url_credentials"}:
                        raise
                    parser = None
                else:
                    if downloaded.status in {401, 403} or downloaded.status >= 500:
                        parser = RobotFileParser()
                        parser.parse(["User-agent: *", "Disallow: /"])
                    elif 200 <= downloaded.status < 300:
                        parser = RobotFileParser()
                        parser.set_url(robots_url)
                        parser.parse(downloaded.body.decode("utf-8", errors="replace").splitlines())
                self.robots[origin] = parser
        if parser is not None and not parser.can_fetch(USER_AGENT, target.url):
            raise LeashByteError(
                403,
                "robots_denied",
                "The destination robots.txt rules do not allow this fetch.",
            )

    async def process(self, downloaded: Downloaded, output_format: str) -> FetchResponse:
        content_type = downloaded.headers.get("content-type", "")
        message = Message()
        message["content-type"] = content_type
        media_type = message.get_content_type().lower()
        is_json = media_type == "application/json" or media_type.endswith("+json")
        is_html = media_type in {"text/html", "application/xhtml+xml"}
        if output_format == "json":
            if not is_json:
                raise LeashByteError(
                    415,
                    "unsupported_content_type",
                    "JSON output needs a JSON response from the destination.",
                )
            try:
                content = json.loads(downloaded.body)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LeashByteError(
                    502,
                    "invalid_json",
                    "The destination returned invalid JSON.",
                ) from error
        elif is_html:
            charset = message.get_content_charset("utf-8")
            try:
                html = downloaded.body.decode(charset, errors="replace")
            except LookupError as error:
                raise LeashByteError(
                    502,
                    "invalid_charset",
                    "The destination named an unknown character encoding.",
                ) from error
            content = await asyncio.to_thread(
                self.extract_html, html, downloaded.final_url, output_format
            )
            if not content:
                raise LeashByteError(
                    422,
                    "extraction_failed",
                    "No readable page content was found.",
                )
        else:
            raise LeashByteError(
                415,
                "unsupported_content_type",
                "The destination content type is not supported.",
            )
        return FetchResponse(
            url=downloaded.source_url,
            final_url=downloaded.final_url,
            status=downloaded.status,
            content_type=media_type,
            format=output_format,
            content=content,
        )

    @staticmethod
    def extract_html(html: str, url: str, output_format: str) -> str | None:
        return trafilatura.extract(
            html,
            url=url,
            output_format=output_format,
            include_links=True,
            include_images=False,
            include_tables=True,
            favor_recall=True,
        )
