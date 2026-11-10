"""URL validation, DNS checks, and destination selection."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .errors import LeashByteError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class Target:
    url: str
    host: str
    port: int
    addresses: tuple[IPAddress, ...]
    allowlisted: bool


def denied_address(address: IPAddress) -> bool:
    """Return whether an address can reach a non-public network."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return denied_address(address.ipv4_mapped)
        if address.sixtofour is not None:
            return denied_address(address.sixtofour)
        if address.teredo is not None:
            return denied_address(address.teredo[1])
        if address in ipaddress.IPv6Network("64:ff9b::/96"):
            return denied_address(ipaddress.IPv4Address(address.packed[-4:]))
    if address == ipaddress.IPv4Address("255.255.255.255"):
        return True
    return (
        not address.is_global
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def internal_hostname(host: str) -> bool:
    if "." not in host:
        return True
    suffixes = (
        ".localhost",
        ".local",
        ".internal",
        ".lan",
        ".corp",
        ".home",
        ".home.arpa",
    )
    return host == "localhost" or host.endswith(suffixes)


class URLGuard:
    def __init__(self, allowlist: set[tuple[str, int]]) -> None:
        self.allowlist = allowlist

    def validate(self, raw_url: str) -> tuple[str, str, int, bool]:
        if any(ord(character) < 32 or ord(character) == 127 for character in raw_url):
            raise LeashByteError(400, "invalid_url", "The URL contains a control character.")
        if "\\" in raw_url:
            raise LeashByteError(400, "invalid_url", "The URL contains an invalid separator.")
        try:
            parts = urlsplit(raw_url)
            port = parts.port
        except ValueError as error:
            raise LeashByteError(400, "invalid_url", "The URL is not valid.") from error
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            raise LeashByteError(400, "invalid_scheme", "Use an HTTP or HTTPS URL.")
        if parts.username is not None or parts.password is not None:
            raise LeashByteError(400, "url_credentials", "The URL must not contain credentials.")
        if not parts.hostname:
            raise LeashByteError(400, "invalid_url", "The URL must contain a host.")
        if not parts.hostname.isascii():
            raise LeashByteError(400, "invalid_url", "Use an ASCII or punycode URL host.")
        if "%" in parts.hostname:
            raise LeashByteError(400, "invalid_url", "The URL host must not contain a zone ID.")
        try:
            host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise LeashByteError(400, "invalid_url", "The URL host is not valid.") from error
        port = port or (443 if scheme == "https" else 80)
        allowlisted = (host, port) in self.allowlist
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if internal_hostname(host) and not allowlisted:
                raise LeashByteError(
                    403, "blocked_host", "The destination host is not public."
                ) from None
        host_for_url = f"[{host}]" if ":" in host else host
        default_port = 443 if scheme == "https" else 80
        netloc = host_for_url if port == default_port else f"{host_for_url}:{port}"
        normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
        return normalized, host, port, allowlisted

    async def resolve(self, raw_url: str) -> Target:
        normalized, host, port, allowlisted = self.validate(raw_url)
        try:
            literal = ipaddress.ip_address(host)
            addresses = (literal,)
        except ValueError:
            try:
                answers = await asyncio.to_thread(
                    socket.getaddrinfo,
                    host,
                    port,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                )
            except socket.gaierror as error:
                raise LeashByteError(
                    502,
                    "dns_failed",
                    f"Could not resolve the destination host {host}.",
                ) from error
            addresses = tuple(
                dict.fromkeys(ipaddress.ip_address(answer[4][0]) for answer in answers)
            )
        if not addresses:
            raise LeashByteError(502, "dns_failed", f"The host {host} returned no addresses.")
        if not allowlisted and any(denied_address(address) for address in addresses):
            raise LeashByteError(
                403,
                "blocked_address",
                "The destination host uses a blocked network address.",
            )
        return Target(normalized, host, port, addresses, allowlisted)
