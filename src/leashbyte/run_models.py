"""Run requests, bounded model actions, and public run responses."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RunLimits(BaseModel):
    """User limits that the worker cannot exceed."""

    model_config = ConfigDict(extra="forbid")

    steps: int = Field(default=12, ge=1, le=100)
    elapsed_seconds: int = Field(default=600, ge=10, le=3600)
    tokens: int = Field(default=12_000, ge=100, le=100_000)
    navigations: int = Field(default=5, ge=0, le=50)
    artifact_bytes: int = Field(default=5_000_000, ge=1_024, le=20_000_000)


class RunCreateRequest(BaseModel):
    """A goal and the fixed policy for one agent run."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    start_url: str = Field(min_length=1, max_length=2048)
    profile: str = Field(default="agent", min_length=1, max_length=64)
    provider: Literal["local", "anthropic"] = "local"
    model: str | None = Field(default=None, max_length=128)
    credential_aliases: list[str] = Field(default_factory=list, max_length=10)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    limits: RunLimits = Field(default_factory=RunLimits)

    @field_validator("goal", "start_url")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value: str) -> str:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) is None:
            raise ValueError("Use lowercase letters, digits, underscores and hyphens.")
        return value

    @field_validator("credential_aliases")
    @classmethod
    def valid_aliases(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("Use each credential alias once.")
        for value in values:
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) is None:
                raise ValueError("A credential alias is not valid.")
        return values

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value) is None:
            raise ValueError("The model name is not valid.")
        return value

    @model_validator(mode="after")
    def anthropic_model_is_fixed(self) -> "RunCreateRequest":
        if self.provider == "anthropic" and self.model not in {None, "claude-haiku-5-5"}:
            raise ValueError("This release supports only claude-haiku-5-5 for Claude runs.")
        return self


class AgentAction(BaseModel):
    """The only action shape a model can return."""

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "navigate", "click", "type", "fill_secret", "select", "scroll", "wait", "done", "fail"
    ]
    snapshot_id: str = Field(min_length=8, max_length=64)
    mark: int | None = Field(default=None, ge=1, le=200)
    url: str | None = Field(default=None, max_length=2048)
    text: str | None = Field(default=None, max_length=10_000)
    values: list[str] | None = Field(default=None, max_length=20)
    placeholder: str | None = Field(default=None, max_length=256)
    x: int = Field(default=0, ge=-10_000, le=10_000)
    y: int = Field(default=0, ge=-10_000, le=10_000)
    wait_ms: int = Field(default=250, ge=50, le=2000)
    result: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def secret_placeholder_compatibility(self) -> "AgentAction":
        if (
            self.action == "fill_secret"
            and self.placeholder is None
            and self.values is not None
            and len(self.values) == 1
            and re.fullmatch(
                r"\{\{credential:[a-z0-9][a-z0-9_-]{0,63}\.(?:username|password)\}\}",
                self.values[0],
            )
        ):
            self.placeholder = self.values[0]
            self.values = None
        return self

    def browser_payload(self) -> dict:
        """Remove fields that belong only to the model protocol."""
        return self.model_dump(
            include={
                "action",
                "snapshot_id",
                "mark",
                "url",
                "text",
                "values",
                "placeholder",
                "x",
                "y",
            },
            exclude_none=True,
        )


class CancelRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="Cancelled by the user.", min_length=1, max_length=300)


class RunResponse(BaseModel):
    id: str
    goal: str
    state: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    provider: str
    model: str
    profile: str
    session_id: str | None
    start_url: str
    step_count: int
    usage: dict
    result: dict | None
    error: str | None
    cancellation_reason: str | None
    artifact_bytes: int


class RunListResponse(BaseModel):
    runs: list[RunResponse]
    next_cursor: str | None


class StepResponse(BaseModel):
    number: int
    snapshot_id: str
    action: dict
    before_observation: dict
    after_observation: dict | None
    source_url: str | None
    error: str | None
    created_at: str
