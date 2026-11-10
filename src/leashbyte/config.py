"""Environment configuration and private-network allowlist parsing."""

import base64
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEASHBYTE_",
        env_file=".env",
        extra="ignore",
    )

    api_key: SecretStr | None = None
    api_key_file: Path | None = None
    service_key: SecretStr | None = None
    service_key_file: Path | None = None
    vault_resolve_key: SecretStr | None = None
    vault_resolve_key_file: Path | None = None
    vault_manage_key: SecretStr | None = None
    vault_manage_key_file: Path | None = None
    vault_master_key: SecretStr | None = None
    vault_master_key_file: Path | None = None
    cdp_key: SecretStr | None = None
    cdp_key_file: Path | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_api_key_file: Path | None = None
    local_model_api_key: SecretStr | None = None
    local_model_api_key_file: Path | None = None
    database_dsn: SecretStr = SecretStr("postgresql://leashbyte:leashbyte@run-store:5432/leashbyte")
    run_artifact_root: Path = Path("/data/run-artifacts")
    local_model_base_url: str = "http://host.docker.internal:11434/v1"
    local_model_name: str = ""
    model_timeout_seconds: float = Field(default=120, gt=0, le=300)
    agent_poll_seconds: float = Field(default=0.5, gt=0, le=10)
    agent_max_concurrency: int = Field(default=1, ge=1, le=2)
    bind_address: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    searxng_base_url: str = "http://searxng:8080"
    browser_pool_base_url: str = "http://browser-pool:8082"
    vault_base_url: str = "http://vault:8081"
    search_timeout_seconds: float = Field(default=10, gt=0, le=60)
    search_max_results: int = Field(default=10, ge=1, le=50)
    fetch_timeout_seconds: float = Field(default=20, gt=0, le=120)
    fetch_connect_timeout_seconds: float = Field(default=5, gt=0, le=30)
    fetch_max_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)
    fetch_max_redirects: int = Field(default=5, ge=0, le=20)
    fetch_max_concurrency: int = Field(default=4, ge=1, le=32)
    fetch_min_host_interval_seconds: float = Field(default=1, ge=0, le=60)
    private_network_allowlist: str = ""
    respect_robots: bool = True
    browser_max_sessions: int = Field(default=2, ge=1, le=8)
    browser_lease_seconds: int = Field(default=900, ge=30, le=3600)
    browser_navigation_timeout_seconds: float = Field(default=20, gt=0, le=120)
    browser_profile_root: Path = Path("/data/profiles")
    cdp_grant_seconds: int = Field(default=60, ge=15, le=120)
    browser_tls_spki_allowlist: str = ""
    browser_plugins_enabled: bool = False
    browser_plugin_modules: str = ""
    web_bot_auth_key_file: Path | None = None
    web_bot_auth_key_id: str = ""
    web_bot_auth_agent_url: str = "https://leashbyte.com"

    @field_validator(
        "api_key_file",
        "service_key_file",
        "vault_resolve_key_file",
        "vault_manage_key_file",
        "vault_master_key_file",
        "cdp_key_file",
        "anthropic_api_key_file",
        "local_model_api_key_file",
        "web_bot_auth_key_file",
        mode="before",
    )
    @classmethod
    def empty_file_is_unset(cls, value):
        return value or None

    @model_validator(mode="after")
    def connect_timeout_fits_total_timeout(self) -> "Settings":
        if self.fetch_connect_timeout_seconds > self.fetch_timeout_seconds:
            raise ValueError("The connection timeout must not exceed the total timeout.")
        return self

    @staticmethod
    def _read_secret(direct: SecretStr | None, file: Path | None, name: str) -> str:
        value = ""
        if file is not None:
            value = file.read_text(encoding="utf-8").strip()
        elif direct is not None:
            value = direct.get_secret_value().strip()
        if not value:
            raise RuntimeError(f"Set {name} or {name}_FILE before starting LeashByte.")
        if value.startswith("replace-with-"):
            raise RuntimeError(
                f"Replace the example placeholder in {name} before starting LeashByte."
            )
        if len(value) > 4096:
            raise RuntimeError(f"The {name} value is too long.")
        return value

    def require_api_key(self) -> str:
        return self._read_secret(self.api_key, self.api_key_file, "LEASHBYTE_API_KEY")

    def require_service_key(self) -> str:
        return self._read_secret(self.service_key, self.service_key_file, "LEASHBYTE_SERVICE_KEY")

    def require_vault_resolve_key(self) -> str:
        return self._read_secret(
            self.vault_resolve_key,
            self.vault_resolve_key_file,
            "LEASHBYTE_VAULT_RESOLVE_KEY",
        )

    def require_vault_manage_key(self) -> str:
        return self._read_secret(
            self.vault_manage_key,
            self.vault_manage_key_file,
            "LEASHBYTE_VAULT_MANAGE_KEY",
        )

    def require_cdp_key(self) -> str:
        return self._read_secret(self.cdp_key, self.cdp_key_file, "LEASHBYTE_CDP_KEY")

    def require_anthropic_api_key(self) -> str:
        return self._read_secret(
            self.anthropic_api_key,
            self.anthropic_api_key_file,
            "LEASHBYTE_ANTHROPIC_API_KEY",
        )

    def optional_local_model_api_key(self) -> str | None:
        if self.local_model_api_key_file is None and (
            self.local_model_api_key is None
            or not self.local_model_api_key.get_secret_value().strip()
        ):
            return None
        return self._read_secret(
            self.local_model_api_key,
            self.local_model_api_key_file,
            "LEASHBYTE_LOCAL_MODEL_API_KEY",
        )

    def require_vault_master_key(self) -> bytes:
        value = self._read_secret(
            self.vault_master_key,
            self.vault_master_key_file,
            "LEASHBYTE_VAULT_MASTER_KEY",
        )
        try:
            if len(value) == 64:
                key = bytes.fromhex(value)
            else:
                key = base64.urlsafe_b64decode(value.encode("ascii"))
        except (ValueError, UnicodeError) as error:
            raise RuntimeError("The vault master key must be hex or base64.") from error
        if len(key) != 32:
            raise RuntimeError("The vault master key must decode to 32 bytes.")
        return key

    def allowlist(self) -> set[tuple[str, int]]:
        entries: set[tuple[str, int]] = set()
        for raw_entry in self.private_network_allowlist.split(","):
            entry = raw_entry.strip()
            if not entry:
                continue
            if entry.startswith("["):
                closing = entry.find("]")
                if closing < 0 or entry[closing + 1 : closing + 2] != ":":
                    raise RuntimeError("An allowlist entry has an invalid IPv6 host or port.")
                host = entry[1:closing]
                raw_port = entry[closing + 2 :]
            else:
                host, separator, raw_port = entry.rpartition(":")
                if not separator:
                    raise RuntimeError("An allowlist entry must include a host and port.")
            host = host.rstrip(".").encode("idna").decode("ascii").lower()
            port = int(raw_port)
            if not host or not 1 <= port <= 65535:
                raise RuntimeError("An allowlist entry has an invalid host or port.")
            entries.add((host, port))
        return entries
