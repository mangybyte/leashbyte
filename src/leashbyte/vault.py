"""Encrypted, origin-bound credential storage."""

import base64
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import LeashByteError

ALIAS_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def exact_https_origin(raw_origin: str) -> str:
    """Return a canonical HTTPS origin, or reject the grant."""
    if not raw_origin.isascii():
        raise LeashByteError(422, "invalid_origin", "Use an ASCII or punycode origin.")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_origin):
        raise LeashByteError(422, "invalid_origin", "An allowed origin is not valid.")
    if "\\" in raw_origin:
        raise LeashByteError(422, "invalid_origin", "An allowed origin is not valid.")
    try:
        parts = urlsplit(raw_origin)
        port = parts.port
        host = parts.hostname
        if (
            parts.scheme.lower() != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ValueError
        canonical_host = host.rstrip(".").encode("idna").decode("ascii").lower()
        if not canonical_host or "%" in canonical_host or "*" in canonical_host:
            raise ValueError
        canonical_port = port or 443
    except (ValueError, UnicodeError) as error:
        raise LeashByteError(
            422,
            "invalid_origin",
            "Each credential grant must be one exact HTTPS origin.",
        ) from error
    host_for_url = f"[{canonical_host}]" if ":" in canonical_host else canonical_host
    suffix = "" if canonical_port == 443 else f":{canonical_port}"
    return f"https://{host_for_url}{suffix}"


def https_page_origin(raw_url: str) -> str:
    """Reduce an HTTPS page URL to its origin before a credential fill."""
    if (
        any(ord(character) < 32 or ord(character) == 127 for character in raw_url)
        or "\\" in raw_url
    ):
        raise LeashByteError(403, "origin_not_allowed", "This origin cannot use the credential.")
    try:
        parts = urlsplit(raw_url)
        if (
            parts.scheme.lower() != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError
    except ValueError as error:
        raise LeashByteError(
            403, "origin_not_allowed", "This origin cannot use the credential."
        ) from error
    return exact_https_origin(f"https://{parts.netloc}")


def placeholder_parts(placeholder: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"\{\{credential:([a-z0-9][a-z0-9_-]{0,63})\.(username|password)\}\}", placeholder
    )
    if match is None:
        raise LeashByteError(422, "invalid_placeholder", "The credential placeholder is not valid.")
    return match.group(1), match.group(2)


class Vault:
    """AES-GCM envelope encryption with a random data key for each credential."""

    def __init__(self, path: Path, master_key: bytes) -> None:
        self.path = path
        self.master_key = AESGCM(master_key)
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.b64decode(value.encode("ascii"), validate=True)

    @staticmethod
    def _aad(alias: str, origins: list[str]) -> bytes:
        return json.dumps(
            {"alias": alias, "allowed_origins": origins},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "credentials": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("credentials"), dict):
                raise ValueError
            return data
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise LeashByteError(
                500, "vault_read_failed", "The vault could not be read."
            ) from error

    def _save(self, data: dict) -> None:
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output, sort_keys=True, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def put(self, alias: str, username: str, password: str, raw_origins: list[str]) -> dict:
        if ALIAS_PATTERN.fullmatch(alias) is None:
            raise LeashByteError(422, "invalid_alias", "The credential alias is not valid.")
        origins = sorted({exact_https_origin(origin) for origin in raw_origins})
        if not origins:
            raise LeashByteError(422, "invalid_origin", "Add at least one allowed HTTPS origin.")
        plaintext = json.dumps({"username": username, "password": password}).encode("utf-8")
        aad = self._aad(alias, origins)
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        wrap_nonce = os.urandom(12)
        record = {
            "allowed_origins": origins,
            "nonce": self._encode(nonce),
            "ciphertext": self._encode(AESGCM(data_key).encrypt(nonce, plaintext, aad)),
            "wrap_nonce": self._encode(wrap_nonce),
            "wrapped_key": self._encode(self.master_key.encrypt(wrap_nonce, data_key, aad)),
        }
        with self.lock:
            data = self._load()
            data["credentials"][alias] = record
            self._save(data)
        return {"alias": alias, "allowed_origins": origins}

    def list(self) -> list[dict]:
        with self.lock:
            data = self._load()
        return [
            {"alias": alias, "allowed_origins": record["allowed_origins"]}
            for alias, record in sorted(data["credentials"].items())
        ]

    def delete(self, alias: str) -> None:
        with self.lock:
            data = self._load()
            if alias not in data["credentials"]:
                raise LeashByteError(404, "credential_not_found", "The credential was not found.")
            del data["credentials"][alias]
            self._save(data)

    def resolve(self, alias: str, field: str, raw_origin: str) -> str:
        if field not in {"username", "password"} or ALIAS_PATTERN.fullmatch(alias) is None:
            raise LeashByteError(
                422, "invalid_credential_request", "The credential request is not valid."
            )
        origin = https_page_origin(raw_origin)
        with self.lock:
            data = self._load()
            record = data["credentials"].get(alias)
        if record is None:
            raise LeashByteError(404, "credential_not_found", "The credential was not found.")
        if origin not in record["allowed_origins"]:
            raise LeashByteError(
                403, "origin_not_allowed", "This origin cannot use the credential."
            )
        try:
            aad = self._aad(alias, record["allowed_origins"])
            data_key = self.master_key.decrypt(
                self._decode(record["wrap_nonce"]),
                self._decode(record["wrapped_key"]),
                aad,
            )
            plaintext = AESGCM(data_key).decrypt(
                self._decode(record["nonce"]),
                self._decode(record["ciphertext"]),
                aad,
            )
            values = json.loads(plaintext)
            return values[field]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise LeashByteError(
                500, "vault_decrypt_failed", "The credential could not be read."
            ) from error
