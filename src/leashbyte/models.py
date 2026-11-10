"""API request and response models."""

import re
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    fields: dict[str, str] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    format: Literal["markdown", "html", "json"] = "markdown"
    timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    max_bytes: int | None = Field(default=None, gt=0, le=20_000_000)

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()


class FetchResponse(BaseModel):
    url: str
    final_url: str
    status: int
    content_type: str
    format: Literal["markdown", "html", "json"]
    content: str | dict | list | int | float | bool | None


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


class ConfigResponse(BaseModel):
    version: str
    features: dict[str, bool]
    limits: dict[str, int | float]


class BrowserSessionRequest(BaseModel):
    profile: str = Field(min_length=1, max_length=64)
    start_url: str | None = Field(default=None, max_length=2048)
    proxy: str | None = Field(default=None, max_length=2048)
    lease_seconds: int | None = Field(default=None, ge=30, le=3600)

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
            raise ValueError("Use lowercase letters, digits, underscores and hyphens.")
        return value


class BrowserSessionResponse(BaseModel):
    id: str
    profile: str
    state: str
    current_url: str
    expires_at: str
    capabilities: list[str]


class BrowserCloseResponse(BaseModel):
    id: str
    state: str
    profile_released: bool


class BrowserNode(BaseModel):
    mark: int
    tag: str
    role: str
    name: str
    input_type: str | None


class BrowserSnapshotResponse(BaseModel):
    session_id: str
    snapshot_id: str
    url: str
    title: str
    nodes: list[BrowserNode]
    text: str = ""
    text_truncated: bool = False


class CdpGrantResponse(BaseModel):
    session_id: str
    token: str
    expires_in: int
    path: str


class CredentialMetadata(BaseModel):
    alias: str
    allowed_origins: list[str]


class CredentialListResponse(BaseModel):
    credentials: list[CredentialMetadata]


class BrowserActionRequest(BaseModel):
    action: Literal["navigate", "click", "type", "select", "scroll", "fill_secret"]
    snapshot_id: str = Field(min_length=8, max_length=64)
    mark: int | None = Field(default=None, ge=1)
    url: str | None = Field(default=None, max_length=2048)
    text: str | None = Field(default=None, max_length=100_000)
    values: list[str] | None = Field(default=None, max_length=20)
    placeholder: str | None = Field(default=None, max_length=256)
    x: int = Field(default=0, ge=-10_000, le=10_000)
    y: int = Field(default=0, ge=-10_000, le=10_000)


class CredentialRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=64)
    username: SecretStr = Field(min_length=1, max_length=4096)
    password: SecretStr = Field(min_length=1, max_length=16_384)
    allowed_origins: list[str] = Field(min_length=1, max_length=20)

    @field_validator("alias")
    @classmethod
    def valid_alias(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
            raise ValueError("Use lowercase letters, digits, underscores and hyphens.")
        return value
