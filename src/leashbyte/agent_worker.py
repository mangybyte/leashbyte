"""Claim and execute bounded observe-act-observe agent runs."""

import asyncio
import logging
import re
import time
import uuid
from contextlib import suppress
from datetime import datetime
from urllib.parse import urlsplit

from .config import Settings
from .errors import LeashByteError
from .internal import internal_request, response_json
from .model_adapter import ClaudeModel, LocalModel, observation_message
from .run_models import AgentAction
from .run_store import RunStore, json_value, utcnow
from .vault import https_page_origin, placeholder_parts

logger = logging.getLogger("leashbyte.worker")


def run_origin(raw_url: str) -> str:
    """Return an exact HTTP or HTTPS origin for run policy."""
    try:
        parts = urlsplit(raw_url)
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except (ValueError, UnicodeError) as error:
        raise LeashByteError(422, "invalid_origin", "A run origin is not valid.") from error
    rendered_host = f"[{host}]" if ":" in host else host
    default_port = 443 if parts.scheme.lower() == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return f"{parts.scheme.lower()}://{rendered_host}{suffix}"


def contains_secret_shaped_text(value: str | None) -> bool:
    """Block common secret markers even when a model uses the wrong action."""
    return bool(
        value
        and re.search(r"(?i)(password|secret|credential|api[_-]?key|bearer\s|token\s*[=:])", value)
    )


def validate_action(action: AgentAction, observation: dict, policy: dict, navigations: int) -> None:
    """Reject stale, malformed, or out-of-policy model output before browser use."""
    if action.snapshot_id != observation.get("snapshot_id"):
        raise LeashByteError(409, "stale_snapshot", "The model used a stale snapshot.")
    name = action.action
    if name == "navigate":
        if not action.url:
            raise LeashByteError(422, "invalid_action", "Navigation needs a URL.")
        if run_origin(action.url) not in policy["allowed_origins"]:
            raise LeashByteError(403, "origin_not_allowed", "The run policy denies this origin.")
        if navigations >= policy["limits"]["navigations"]:
            raise LeashByteError(409, "navigation_limit", "The run reached its navigation limit.")
    elif name in {"click", "type", "fill_secret", "select"}:
        marks = {node.get("mark") for node in observation.get("nodes", [])}
        if action.mark not in marks:
            raise LeashByteError(422, "invalid_mark", "The model used an unknown mark.")
    if contains_secret_shaped_text(action.text) or contains_secret_shaped_text(action.url):
        raise LeashByteError(
            403, "secret_value_rejected", "Use a credential placeholder for secret values."
        )
    if any(contains_secret_shaped_text(value) for value in (action.values or [])):
        raise LeashByteError(
            403, "secret_value_rejected", "Use a credential placeholder for secret values."
        )
    if name == "type":
        if action.text is None or "{{credential:" in action.text:
            raise LeashByteError(422, "invalid_action", "Use public text for a type action.")
    if name == "select" and not action.values:
        raise LeashByteError(422, "invalid_action", "Select needs at least one value.")
    if name == "fill_secret":
        if not action.placeholder:
            raise LeashByteError(422, "invalid_action", "Secret fill needs a placeholder.")
        alias, _ = placeholder_parts(action.placeholder)
        if alias not in policy["credential_aliases"]:
            raise LeashByteError(403, "credential_not_granted", "The run policy denies this alias.")
        if action.placeholder not in observation.get("credential_placeholders", []):
            raise LeashByteError(
                403, "origin_not_allowed", "This origin cannot use the credential."
            )
    if name == "done" and not action.result:
        raise LeashByteError(422, "invalid_action", "A completed run needs a result.")
    if name == "fail" and not action.reason:
        raise LeashByteError(422, "invalid_action", "A failed model action needs a reason.")


class AgentWorker:
    def __init__(self, settings: Settings, store: RunStore) -> None:
        self.settings = settings
        self.store = store

    async def browser(self, method: str, path: str, payload: dict | None = None):
        response = await internal_request(
            self.settings.browser_pool_base_url,
            self.settings.require_service_key(),
            method,
            path,
            payload,
            timeout=self.settings.browser_navigation_timeout_seconds + 30,
        )
        return response_json(response)

    @staticmethod
    async def within_deadline(awaitable, deadline: float):
        """Stop a model or browser call when the run reaches its time limit."""
        try:
            return await asyncio.wait_for(
                awaitable, timeout=max(0.001, deadline - time.monotonic())
            )
        except TimeoutError as error:
            raise LeashByteError(409, "time_limit", "The run reached its time limit.") from error

    @staticmethod
    def output_token_allowance(observation: dict, remaining_tokens: int) -> int:
        """Reserve room for input and bound one structured action response."""
        if remaining_tokens < 100:
            raise LeashByteError(409, "token_limit", "The run reached its token limit.")
        estimated_input = (len(observation_message(observation).encode("utf-8")) + 2) // 3
        if estimated_input + 100 > remaining_tokens:
            raise LeashByteError(409, "token_limit", "The run reached its token limit.")
        return min(2048, remaining_tokens - estimated_input)

    async def credentials(self) -> list[dict]:
        response = await internal_request(
            self.settings.vault_base_url,
            self.settings.require_vault_manage_key(),
            "GET",
            "/internal/credentials",
        )
        body = response_json(response)
        if not isinstance(body, dict) or not isinstance(body.get("credentials"), list):
            raise LeashByteError(
                502, "invalid_service_response", "The vault returned invalid data."
            )
        return body["credentials"]

    def model_for(self, run: dict):
        if run["provider"] == "anthropic":
            try:
                api_key = self.settings.require_anthropic_api_key()
            except RuntimeError as error:
                raise LeashByteError(503, "model_key_unavailable", str(error)[:300]) from error
            return ClaudeModel(api_key, run["model"], self.settings.model_timeout_seconds)
        return LocalModel(
            self.settings.local_model_base_url,
            run["model"],
            self.settings.optional_local_model_api_key(),
            self.settings.model_timeout_seconds,
        )

    @staticmethod
    def observation(raw: dict, run: dict, policy: dict, credentials: list[dict]) -> dict:
        origin = run_origin(raw["url"])
        if origin not in policy["allowed_origins"]:
            raise LeashByteError(403, "origin_not_allowed", "The page left the allowed origins.")
        placeholders = []
        try:
            credential_origin = https_page_origin(raw["url"])
        except LeashByteError:
            credential_origin = ""
        for credential in credentials:
            if (
                credential["alias"] in policy["credential_aliases"]
                and credential_origin in credential["allowed_origins"]
            ):
                placeholders.extend(
                    [
                        "{{credential:" + credential["alias"] + ".username}}",
                        "{{credential:" + credential["alias"] + ".password}}",
                    ]
                )
        return {
            "goal": run["goal"],
            "limits": policy["limits"],
            "allowed_origins": policy["allowed_origins"],
            "snapshot_id": raw["snapshot_id"],
            "origin": origin,
            "url": raw["url"],
            "title": raw["title"],
            "nodes": raw["nodes"],
            "text": raw.get("text", ""),
            "text_truncated": raw.get("text_truncated", False),
            "credential_placeholders": placeholders,
        }

    async def screenshot(self, run_id: str, session_id: str, number: int, limit: int) -> None:
        response = await internal_request(
            self.settings.browser_pool_base_url,
            self.settings.require_service_key(),
            "GET",
            f"/internal/sessions/{session_id}/screenshot",
        )
        await self.store.add_artifact(
            run_id, f"step-{number:04d}.png", "image/png", response.content, limit
        )

    async def execute(self, claimed: dict) -> None:
        run_id = claimed["id"]
        raw = await self.store.raw(run_id)
        policy = raw["policy"]
        if not policy["allowed_origins"]:
            policy["allowed_origins"] = [run_origin(raw["start_url"])]
        else:
            policy["allowed_origins"] = sorted(
                {run_origin(item) for item in policy["allowed_origins"]}
            )
            if run_origin(raw["start_url"]) not in policy["allowed_origins"]:
                raise LeashByteError(
                    422, "origin_not_allowed", "The start URL must use an allowed origin."
                )
        policy["limits"] = {key: int(value) for key, value in policy["limits"].items()}
        usage = dict(raw["usage"])
        session_id = None
        steps = 0
        deadline = time.monotonic() + policy["limits"]["elapsed_seconds"]
        terminal_state = "failed"
        result = None
        error_message = None
        cancellation_reason = None
        final_observation = None
        try:
            credentials = await self.credentials()
            model = self.model_for(raw)
            created = await self.within_deadline(
                self.browser(
                    "POST",
                    "/internal/sessions",
                    {
                        "profile": raw["profile"],
                        "start_url": raw["start_url"],
                        "lease_seconds": min(policy["limits"]["elapsed_seconds"] + 30, 3600),
                    },
                ),
                deadline,
            )
            session_id = created["id"]
            lease_expires = datetime.fromisoformat(created["expires_at"]).timestamp()
            deadline = min(deadline, time.monotonic() + max(0.0, lease_expires - time.time()))
            await self.store.set_session(run_id, session_id)
            current = await self.within_deadline(
                self.browser("GET", f"/internal/sessions/{session_id}/snapshot"), deadline
            )
            while True:
                await self.check_control(run_id, deadline, usage, policy)
                observed = self.observation(current, raw, policy, credentials)
                previous = await self.store.steps(run_id)
                observed["previous_actions"] = [
                    {
                        "action": step["action"],
                        "source_url": step["source_url"],
                        "result": (
                            {
                                "url": step["after_observation"]["url"],
                                "title": step["after_observation"]["title"],
                            }
                            if step["after_observation"] is not None
                            else None
                        ),
                        "error": step["error"],
                    }
                    for step in previous[-5:]
                ]
                remaining_tokens = policy["limits"]["tokens"] - usage["total_tokens"]
                requested_output = self.output_token_allowance(observed, remaining_tokens)
                decision = await self.within_deadline(
                    model.decide(observed, requested_output), deadline
                )
                for key in usage:
                    if key in decision.usage:
                        usage[key] += decision.usage[key]
                await self.store.update_usage(run_id, usage)
                steps += 1
                action = decision.action
                step_recorded = False
                try:
                    validate_action(action, observed, policy, usage["navigations"])
                    await self.check_control(run_id, deadline, usage, policy)
                    if usage["total_tokens"] > policy["limits"]["tokens"]:
                        raise LeashByteError(409, "token_limit", "The run reached its token limit.")
                    previous_url = current["url"]
                    if action.action in {"done", "fail"}:
                        current = await self.within_deadline(
                            self.browser("GET", f"/internal/sessions/{session_id}/snapshot"),
                            deadline,
                        )
                    elif action.action == "wait":
                        await self.within_deadline(asyncio.sleep(action.wait_ms / 1000), deadline)
                        current = await self.within_deadline(
                            self.browser("GET", f"/internal/sessions/{session_id}/snapshot"),
                            deadline,
                        )
                    else:
                        current = await self.within_deadline(
                            self.browser(
                                "POST",
                                f"/internal/sessions/{session_id}/actions",
                                action.browser_payload(),
                            ),
                            deadline,
                        )
                    if current["url"] != previous_url:
                        usage["navigations"] += 1
                        await self.store.update_usage(run_id, usage)
                        if usage["navigations"] > policy["limits"]["navigations"]:
                            raise LeashByteError(
                                409,
                                "navigation_limit",
                                "The run reached its navigation limit.",
                            )
                    final_observation = self.observation(current, raw, policy, credentials)
                    await self.store.add_step(
                        run_id,
                        steps,
                        action.model_dump(mode="json", exclude_none=True),
                        observed,
                        final_observation,
                    )
                    step_recorded = True
                    try:
                        await self.within_deadline(
                            self.screenshot(
                                run_id, session_id, steps, policy["limits"]["artifact_bytes"]
                            ),
                            deadline,
                        )
                    except LeashByteError as screenshot_error:
                        if screenshot_error.code != "screenshot_redaction_active":
                            raise
                    if action.action == "done":
                        terminal_state = "succeeded"
                        result = {
                            "summary": action.result,
                            "source_urls": sorted(
                                {
                                    step["source_url"]
                                    for step in await self.store.steps(run_id)
                                    if step["source_url"]
                                }
                            ),
                        }
                        break
                    if action.action == "fail":
                        terminal_state = "failed"
                        error_message = action.reason[:300]
                        break
                    if steps >= policy["limits"]["steps"]:
                        raise LeashByteError(409, "step_limit", "The run reached its step limit.")
                except LeashByteError as action_error:
                    if not step_recorded:
                        await self.store.add_step(
                            run_id,
                            steps,
                            action.model_dump(mode="json", exclude_none=True),
                            observed,
                            None,
                            action_error.message[:300],
                        )
                    raise
        except LeashByteError as run_error:
            error_message = run_error.message[:300]
            if run_error.code.endswith("_limit"):
                terminal_state = "limit_reached"
            elif run_error.code == "run_cancelled":
                terminal_state = "cancelled"
                cancellation_reason = run_error.message[:300]
            else:
                terminal_state = "failed"
        except Exception as run_error:
            logger.error("Run failed run_id=%s type=%s", run_id, type(run_error).__name__)
            error_message = "The run could not be completed."
            terminal_state = "failed"
        finally:
            if session_id is not None:
                with suppress(LeashByteError):
                    await self.browser("DELETE", f"/internal/sessions/{session_id}")
            try:
                await self.write_final_artifacts(run_id, result, error_message, policy)
            except Exception as artifact_error:
                logger.error(
                    "Final artifact failed run_id=%s type=%s",
                    run_id,
                    type(artifact_error).__name__,
                )
                result = None
                if (
                    isinstance(artifact_error, LeashByteError)
                    and artifact_error.code == "artifact_limit"
                ):
                    terminal_state = "limit_reached"
                    error_message = artifact_error.message
                else:
                    terminal_state = "failed"
                    error_message = "The run artifacts could not be stored."
            await self.store.finish(
                run_id, terminal_state, result, error_message, cancellation_reason
            )

    async def check_control(self, run_id: str, deadline: float, usage: dict, policy: dict) -> None:
        raw = await self.store.raw(run_id)
        if raw["cancel_requested"]:
            raise LeashByteError(
                409, "run_cancelled", raw["cancellation_reason"] or "Cancelled by the user."
            )
        if time.monotonic() >= deadline:
            raise LeashByteError(409, "time_limit", "The run reached its time limit.")
        if usage["total_tokens"] >= policy["limits"]["tokens"]:
            raise LeashByteError(409, "token_limit", "The run reached its token limit.")
        await self.store.heartbeat(run_id)

    async def write_final_artifacts(
        self, run_id: str, result: dict | None, error: str | None, policy: dict
    ) -> None:
        steps = await self.store.steps(run_id)
        events = [
            {
                "number": step["number"],
                "action": step["action"],
                "source_url": step["source_url"],
                "error": step["error"],
            }
            for step in steps
        ]
        await self.store.add_artifact(
            run_id,
            "run-log.json",
            "application/json",
            json_value({"events": events, "error": error}).encode("utf-8"),
            policy["limits"]["artifact_bytes"],
        )
        if result is not None:
            await self.store.add_artifact(
                run_id,
                "result.json",
                "application/json",
                json_value(result).encode("utf-8"),
                policy["limits"]["artifact_bytes"],
            )

    def report_claude_key(self) -> None:
        """Say at start when Claude runs cannot work, so the fault is not a surprise later."""
        try:
            self.settings.require_anthropic_api_key()
        except RuntimeError as error:
            if str(error).startswith("Set "):
                logger.info("Claude runs are off: %s", error)
            else:
                logger.warning("Claude runs are unavailable: %s", error)

    async def run(self) -> None:
        self.report_claude_key()
        await self.store.initialize()
        pool = await self.store.connect()
        async with pool.acquire() as connection:
            interrupted = await connection.fetch(
                "SELECT session_id FROM runs WHERE state='running' AND session_id IS NOT NULL"
            )
            for row in interrupted:
                with suppress(LeashByteError):
                    await self.browser("DELETE", f"/internal/sessions/{row['session_id']}")
            await connection.execute(
                """UPDATE runs SET state='failed', error='The worker restarted.',
                    finished_at=$1 WHERE state='running'""",
                utcnow(),
            )
        logger.info("Agent worker ready")
        while True:
            try:
                claimed = await self.store.claim()
                if claimed is None:
                    await asyncio.sleep(self.settings.agent_poll_seconds)
                    continue
                try:
                    await self.execute(claimed)
                except LeashByteError as error:
                    await self.store.finish(claimed["id"], "failed", error=error.message[:300])
                except Exception as error:
                    logger.error(
                        "Worker setup failed run_id=%s type=%s",
                        claimed["id"],
                        type(error).__name__,
                    )
                    await self.store.finish(
                        claimed["id"], "failed", error="The run could not be started."
                    )
            except Exception as error:
                logger.error("Worker iteration failed type=%s", type(error).__name__)
                await asyncio.sleep(self.settings.agent_poll_seconds)


def new_run_id() -> str:
    return uuid.uuid4().hex
