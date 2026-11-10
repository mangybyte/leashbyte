"""Essential agent policy, secret-isolation, artifact, and run API tests."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from leashbyte.agent_worker import AgentWorker, validate_action
from leashbyte.browser import PAGE_TEXT_LIMIT, PAGE_TEXT_SCAN_LIMIT, BrowserPool
from leashbyte.config import Settings
from leashbyte.errors import LeashByteError
from leashbyte.main import create_app
from leashbyte.model_adapter import (
    CLAUDE_ACTION_SCHEMA,
    ClaudeModel,
    LocalModel,
    observation_message,
    parse_action_text,
)
from leashbyte.model_relay import source_allowed
from leashbyte.run_models import AgentAction, RunCreateRequest
from leashbyte.run_store import ArtifactStore

SECRET = "throwaway-only-secret-7f3a"  # noqa: S105 - synthetic marker, not a credential
RUN_ID = "a" * 32


def action(name: str, **values) -> AgentAction:
    values.setdefault("snapshot_id", "snapshot-1")
    return AgentAction(action=name, **values)


def observation() -> dict:
    return {
        "snapshot_id": "snapshot-1",
        "url": "https://fixture.internal/login",
        "nodes": [{"mark": 1, "tag": "input", "input_type": "password"}],
        "credential_placeholders": ["{{credential:shop.password}}"],
    }


def policy() -> dict:
    return {
        "allowed_origins": ["https://fixture.internal"],
        "credential_aliases": ["shop"],
        "limits": {"navigations": 1},
    }


def test_model_rejects_stale_marks_and_policy_violations() -> None:
    with pytest.raises(LeashByteError) as stale:
        validate_action(
            action("click", mark=1, snapshot_id="old-snapshot"), observation(), policy(), 0
        )
    assert stale.value.code == "stale_snapshot"
    with pytest.raises(LeashByteError) as mark:
        validate_action(action("click", mark=2), observation(), policy(), 0)
    assert mark.value.code == "invalid_mark"
    with pytest.raises(LeashByteError) as origin:
        validate_action(
            action("navigate", url="https://other.example/"), observation(), policy(), 0
        )
    assert origin.value.code == "origin_not_allowed"
    with pytest.raises(LeashByteError) as navigation:
        validate_action(
            action("navigate", url="https://fixture.internal/next"), observation(), policy(), 1
        )
    assert navigation.value.code == "navigation_limit"
    with pytest.raises(LeashByteError) as credential:
        validate_action(
            action("fill_secret", mark=1, placeholder="{{credential:other.password}}"),
            observation(),
            policy(),
            0,
        )
    assert credential.value.code in {"invalid_placeholder", "credential_not_granted"}


def test_both_model_request_builders_exclude_resolved_secrets() -> None:
    observed = {
        "goal": "Fill the login form",
        "title": "Controlled form",
        "credential_placeholders": ["{{credential:shop.password}}"],
    }
    local = LocalModel("http://model.invalid/v1", "controlled-model")
    claude = ClaudeModel("test-only-not-a-live-key")
    for payload in (local.request_payload(observed, 512), claude.request_payload(observed, 512)):
        serialized = json.dumps(payload)
        assert SECRET not in serialized
        assert "{{credential:shop.password}}" in serialized
    controlled = dict(observed, title=SECRET)
    assert SECRET in json.dumps(local.request_payload(controlled, 512))


def test_artifact_paths_and_byte_limit_are_enforced(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path)
    with pytest.raises(LeashByteError):
        artifacts.path(RUN_ID, "../other")
    with pytest.raises(LeashByteError):
        artifacts.path("../other", "result.json")
    with pytest.raises(LeashByteError) as limit:
        import asyncio

        asyncio.run(artifacts.write(RUN_ID, "step.png", b"12345", 4, 0))
    assert limit.value.code == "artifact_limit"


def test_model_relay_accepts_only_the_compose_subnet() -> None:
    assert source_allowed("172.29.250.7", "172.29.250.0/24")
    assert not source_allowed("172.29.251.7", "172.29.250.0/24")
    assert not source_allowed("127.0.0.1", "172.29.250.0/24")
    assert not source_allowed("not-an-address", "172.29.250.0/24")


class ControlStore:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.heartbeats = 0

    async def raw(self, run_id: str) -> dict:
        return {
            "cancel_requested": self.cancelled,
            "cancellation_reason": "Stop the controlled run." if self.cancelled else None,
        }

    async def heartbeat(self, run_id: str) -> None:
        self.heartbeats += 1


async def test_cancellation_time_and_token_checks_stop_before_work(tmp_path: Path) -> None:
    settings = Settings(api_key="test-only-key")
    cancelled = ControlStore(cancelled=True)
    active = ControlStore()
    worker = AgentWorker(settings, active)
    cancelled = ControlStore(cancelled=True)
    cancelled_worker = AgentWorker(settings, cancelled)
    with pytest.raises(LeashByteError) as cancelled_stop:
        await cancelled_worker.check_control(
            RUN_ID, time.monotonic() + 10, {"total_tokens": 0}, {"limits": {"tokens": 100}}
        )
    assert cancelled_stop.value.code == "run_cancelled"
    with pytest.raises(LeashByteError) as elapsed:
        await worker.check_control(
            RUN_ID, time.monotonic() - 1, {"total_tokens": 0}, {"limits": {"tokens": 100}}
        )
    assert elapsed.value.code == "time_limit"
    with pytest.raises(LeashByteError) as bounded:
        await worker.within_deadline(asyncio.sleep(1), time.monotonic() - 1)
    assert bounded.value.code == "time_limit"
    with pytest.raises(LeashByteError) as tokens:
        await worker.check_control(
            RUN_ID, time.monotonic() + 10, {"total_tokens": 100}, {"limits": {"tokens": 100}}
        )
    assert tokens.value.code == "token_limit"


def test_output_allowance_supports_reasoning_and_stays_inside_the_run_budget() -> None:
    observed = {"goal": "Complete the controlled goal."}
    assert AgentWorker.output_token_allowance(observed, 12000) == 2048
    estimated_input = (len(observation_message(observed).encode("utf-8")) + 2) // 3
    assert AgentWorker.output_token_allowance(observed, estimated_input + 150) == 150
    with pytest.raises(LeashByteError) as exhausted:
        AgentWorker.output_token_allowance(observed, estimated_input + 99)
    assert exhausted.value.code == "token_limit"


class ApiStore:
    def __init__(self, root: Path) -> None:
        self.artifacts = ArtifactStore(root)
        self.state = "queued"
        self.request = None

    async def initialize(self) -> None:
        return None

    async def ready(self) -> bool:
        return True

    async def create(self, run_id: str, request: RunCreateRequest, model: str) -> dict:
        self.request = request
        self.run_id = run_id
        return self.record(run_id)

    def record(self, run_id: str) -> dict:
        return {
            "id": run_id,
            "goal": "Controlled goal",
            "state": self.state,
            "created_at": "2026-11-08T00:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "provider": "local",
            "model": "controlled-model",
            "profile": "agent",
            "session_id": None,
            "start_url": "https://fixture.internal/",
            "step_count": 0,
            "usage": {"total_tokens": 0},
            "result": None,
            "error": None,
            "cancellation_reason": None,
            "artifact_bytes": 0,
        }

    async def get(self, run_id: str) -> dict:
        if run_id != self.run_id or self.state == "deleted":
            raise LeashByteError(404, "run_not_found", "The run was not found.")
        return self.record(run_id)

    async def cancel(self, run_id: str, reason: str) -> dict:
        self.state = "cancelled"
        return self.record(run_id)

    async def steps(self, run_id: str) -> list:
        await self.get(run_id)
        return []

    async def artifact(self, run_id: str, name: str):
        await self.get(run_id)
        path = self.artifacts.path(run_id, name)
        if not path.is_file():
            raise LeashByteError(404, "artifact_not_found", "The artifact was not found.")
        return path, "application/json", "test-hash"

    async def delete(self, run_id: str) -> None:
        await self.get(run_id)
        self.state = "deleted"
        self.artifacts.delete(run_id)


def test_run_and_artifact_authorization_cancellation_and_deletion(tmp_path: Path) -> None:
    import asyncio

    store = ApiStore(tmp_path)
    body = {"goal": "Controlled goal", "start_url": "https://fixture.internal/"}
    missing_model = TestClient(create_app(Settings(api_key="test-only-key"), run_store=store))
    rejected = missing_model.post("/v1/runs", json=body, headers={"X-API-Key": "test-only-key"})
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "model_required"

    client = TestClient(
        create_app(
            Settings(api_key="test-only-key", local_model_name="controlled-model"),
            run_store=store,
        )
    )
    assert client.post("/v1/runs", json=body).status_code == 401
    created = client.post("/v1/runs", json=body, headers={"X-API-Key": "test-only-key"})
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert client.get(f"/v1/runs/{run_id}/artifacts/result.json").status_code == 401
    assert client.post(f"/v1/runs/{run_id}/cancel").status_code == 401
    asyncio.run(store.artifacts.write(run_id, "result.json", b"{}", 100, 0))
    assert (
        client.get(
            f"/v1/runs/{run_id}/artifacts/result.json", headers={"X-API-Key": "test-only-key"}
        ).status_code
        == 200
    )
    assert (
        client.post(f"/v1/runs/{run_id}/cancel", headers={"X-API-Key": "test-only-key"}).json()[
            "state"
        ]
        == "cancelled"
    )
    assert (
        client.delete(f"/v1/runs/{run_id}", headers={"X-API-Key": "test-only-key"}).status_code
        == 204
    )
    assert not store.artifacts.directory(run_id).exists()
    assert (
        client.get(f"/v1/runs/{run_id}", headers={"X-API-Key": "test-only-key"}).status_code == 404
    )


def test_model_cannot_submit_secret_values() -> None:
    """Every value-bearing field rejects anything that resembles a credential."""
    for name, values in [
        ("type", {"mark": 1, "text": SECRET}),
        ("navigate", {"url": f"https://fixture.internal/?password={SECRET}"}),
        ("select", {"mark": 1, "values": [SECRET]}),
        ("fill_secret", {"mark": 1, "placeholder": f"{{{{credential:{SECRET}.password}}}}"}),
    ]:
        with pytest.raises(LeashByteError):
            validate_action(action(name, **values), observation(), policy(), 0)


def test_local_placeholder_compatibility_preserves_secret_policy() -> None:
    compatible = action("fill_secret", mark=1, values=["{{credential:shop.password}}"])
    assert compatible.placeholder == "{{credential:shop.password}}"
    assert compatible.values is None
    validate_action(compatible, observation(), policy(), 0)
    with pytest.raises(LeashByteError):
        validate_action(action("fill_secret", mark=1, values=[SECRET]), observation(), policy(), 0)


def test_claude_ignores_an_inherited_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://untrusted.invalid")
    claude = ClaudeModel("test-only-not-a-live-key")
    assert str(claude.client.base_url) == "https://api.anthropic.com"


def test_claude_schema_omits_unsupported_constraints() -> None:
    def nodes(value) -> list[dict]:
        if isinstance(value, dict):
            return [value] + [node for child in value.values() for node in nodes(child)]
        if isinstance(value, list):
            return [node for child in value for node in nodes(child)]
        return []

    local_schema = LocalModel("http://model.invalid/v1", "controlled-model").request_payload(
        {"goal": "Complete the controlled goal."}, 512
    )["response_format"]["json_schema"]["schema"]
    claude_schema = ClaudeModel("test-only-not-a-live-key").request_payload(
        {"goal": "Complete the controlled goal."}, 512
    )["output_config"]["format"]["schema"]
    unsupported = {
        "minimum",
        "maximum",
        "multipleOf",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
    local_keys = {key for node in nodes(local_schema) for key in node}
    claude_keys = {key for node in nodes(claude_schema) for key in node}
    assert {"minimum", "maximum", "maxLength", "maxItems"} <= local_keys
    assert claude_keys.isdisjoint(unsupported)
    assert AgentAction(action="wait", snapshot_id="snapshot-1", wait_ms=1).wait_ms == 50
    with pytest.raises(ValueError):
        AgentAction(action="click", snapshot_id="snapshot-1", mark=201)
    with pytest.raises(ValueError):
        AgentAction(action="done", snapshot_id="short")


def test_claude_run_model_is_fixed() -> None:
    assert Settings(local_model_api_key="").optional_local_model_api_key() is None
    with pytest.raises(ValueError):
        RunCreateRequest(
            goal="Controlled goal",
            start_url="https://fixture.internal/",
            provider="anthropic",
            model="unapproved-model",
        )


class TextPage:
    """A page double that returns visible text the way the page script does."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.url = f"https://fixture.internal/?token={SECRET}"

    async def query_selector_all(self, selector: str) -> list:
        return []

    async def evaluate(self, script: str, limit: int) -> dict:
        assert limit == PAGE_TEXT_SCAN_LIMIT
        return {"text": self.text[:limit], "cut": len(self.text) > limit}

    async def title(self) -> str:
        return f"Account {SECRET}"


async def text_snapshot(text: str) -> dict:
    session = SimpleNamespace(
        id="session",
        context=SimpleNamespace(pages=[TextPage(text)]),
        redactions=[SECRET],
        marks={},
        latest_snapshot="",
    )
    pool = SimpleNamespace(redact=BrowserPool.redact, bounded_redact=BrowserPool.bounded_redact)
    return await BrowserPool.snapshot(pool, session)


async def test_page_text_is_redacted_bounded_and_counted() -> None:
    small = await text_snapshot(f"First quote\nAuthor {SECRET} wrote it.")
    assert small["text"] == "First quote\nAuthor [redacted] wrote it."
    assert small["text_truncated"] is False
    assert SECRET not in json.dumps(small)

    large = await text_snapshot(("quote " * 100) + SECRET + ("tail " * 30_000))
    assert len(large["text"]) == PAGE_TEXT_LIMIT
    assert large["text_truncated"] is True
    assert SECRET not in large["text"]

    # A secret that crosses the page script's scan cut is only partly present.
    session = SimpleNamespace(redactions=[SECRET])
    cut, truncated = BrowserPool.bounded_redact(
        session, ("visible text " * 20) + SECRET[:12], True, 1000, 2000
    )
    assert cut.startswith("visible text")
    assert SECRET[:12] not in cut
    assert truncated is True
    tabbed = SimpleNamespace(redactions=["two\tspaced  secret"])
    spaced, _ = BrowserPool.bounded_redact(tabbed, "pw two\tspaced  secret end", False, 1000, 2000)
    assert spaced == "pw [redacted] end"
    oversized, oversized_cut = BrowserPool.bounded_redact(
        session, "x" * (PAGE_TEXT_SCAN_LIMIT * 2), False, PAGE_TEXT_LIMIT, PAGE_TEXT_SCAN_LIMIT
    )
    assert len(oversized) == PAGE_TEXT_LIMIT and oversized_cut is True

    without_text = {"goal": "Complete the controlled goal.", "text": ""}
    with_text = {**without_text, "text": large["text"]}
    assert AgentWorker.output_token_allowance(with_text, 3000) < AgentWorker.output_token_allowance(
        without_text, 3000
    )


def test_invalid_model_action_keeps_the_fault_type_only() -> None:
    # Claude gets no numeric bounds, so a long wait fits the sent schema.
    wait_schema = CLAUDE_ACTION_SCHEMA["properties"]["wait_ms"]
    assert "maximum" not in wait_schema
    assert "maximum 2000" in wait_schema["description"]
    long_wait = parse_action_text('{"action":"wait","snapshot_id":"snapshot-1","wait_ms":5000}')
    assert long_wait.wait_ms == 2000

    reply = json.dumps(
        {"action": "done", "snapshot_id": "snapshot-1", "result": "r" * 4001, SECRET: SECRET}
    )
    with pytest.raises(LeashByteError) as invalid:
        parse_action_text(reply)
    assert invalid.value.code == "invalid_model_action"
    assert "string_too_long at result" in invalid.value.message
    assert "extra_forbidden at unknown_field" in invalid.value.message
    assert SECRET not in invalid.value.message
    assert "rrrr" not in invalid.value.message

    with pytest.raises(LeashByteError) as truncated:
        parse_action_text('{"action":"done","result":"cut', "max_tokens")
    assert "json_invalid, stop reason max_tokens" in truncated.value.message
