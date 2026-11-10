"""PostgreSQL run records and private, bounded run artifacts."""

import asyncio
import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from .errors import LeashByteError
from .run_models import RunCreateRequest

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    cancellation_reason TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    profile TEXT NOT NULL,
    session_id TEXT,
    start_url TEXT NOT NULL,
    policy JSONB NOT NULL,
    usage JSONB NOT NULL,
    result JSONB,
    error TEXT,
    heartbeat_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS runs_created_idx ON runs (created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS run_steps (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    snapshot_id TEXT NOT NULL,
    action JSONB NOT NULL,
    before_observation JSONB NOT NULL,
    after_observation JSONB,
    source_url TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, number)
);
CREATE TABLE IF NOT EXISTS run_artifacts (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, name)
);
"""
RUN_ID = re.compile(r"[a-f0-9]{32}")
ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def utcnow() -> datetime:
    return datetime.now(UTC)


def json_value(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def public_run(row: asyncpg.Record | dict, artifact_bytes: int = 0) -> dict:
    state = row["state"]
    if state == "running" and row["cancel_requested"]:
        state = "cancelling"
    return {
        "id": row["id"],
        "goal": row["goal"],
        "state": state,
        "created_at": row["created_at"].isoformat(),
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "provider": row["provider"],
        "model": row["model"],
        "profile": row["profile"],
        "session_id": row["session_id"],
        "start_url": row["start_url"],
        "step_count": int(row["step_count"]),
        "usage": row["usage"],
        "result": row["result"],
        "error": row["error"],
        "cancellation_reason": row["cancellation_reason"],
        "artifact_bytes": artifact_bytes,
    }


class ArtifactStore:
    """Keep every file below one run directory and apply a byte limit."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def directory(self, run_id: str) -> Path:
        if RUN_ID.fullmatch(run_id) is None:
            raise LeashByteError(404, "run_not_found", "The run was not found.")
        path = self.root / run_id
        if path.is_symlink():
            raise LeashByteError(404, "run_not_found", "The run was not found.")
        return path

    def path(self, run_id: str, name: str) -> Path:
        if ARTIFACT_NAME.fullmatch(name) is None or name in {".", ".."}:
            raise LeashByteError(404, "artifact_not_found", "The artifact was not found.")
        directory = self.directory(run_id)
        path = directory / name
        if path.parent != directory or path.is_symlink():
            raise LeashByteError(404, "artifact_not_found", "The artifact was not found.")
        return path

    async def write(self, run_id: str, name: str, data: bytes, limit: int, used: int) -> dict:
        if len(data) > limit - used:
            raise LeashByteError(409, "artifact_limit", "The run reached its artifact byte limit.")
        path = self.path(run_id, name)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError as error:
            raise LeashByteError(409, "artifact_exists", "The artifact already exists.") from error
        with os.fdopen(descriptor, "wb") as output:
            await asyncio.to_thread(output.write, data)
        return {
            "name": name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def delete(self, run_id: str) -> None:
        directory = self.directory(run_id)
        if directory.exists():
            resolved_root = self.root.resolve()
            resolved = directory.resolve()
            if resolved.parent != resolved_root:
                raise LeashByteError(409, "unsafe_artifact_path", "The artifact path is not safe.")
            shutil.rmtree(directory)


class RunStore:
    """Share run claims and records between the API and workers."""

    def __init__(self, dsn: str, artifacts: ArtifactStore) -> None:
        self.dsn = dsn
        self.artifacts = artifacts
        self.pool: asyncpg.Pool | None = None
        self.connect_lock = asyncio.Lock()

    @staticmethod
    async def _initialize_connection(connection: asyncpg.Connection) -> None:
        """Accept JSON strings on writes and return structured values on reads."""
        for type_name in ("json", "jsonb"):
            await connection.set_type_codec(
                type_name, schema="pg_catalog", encoder=str, decoder=json.loads, format="text"
            )

    async def connect(self) -> asyncpg.Pool:
        if self.pool is None:
            async with self.connect_lock:
                if self.pool is None:
                    try:
                        self.pool = await asyncpg.create_pool(
                            dsn=self.dsn,
                            min_size=1,
                            max_size=4,
                            command_timeout=15,
                            init=self._initialize_connection,
                        )
                    except (OSError, asyncpg.PostgresError) as error:
                        raise LeashByteError(
                            503, "run_store_unavailable", "The run store is not available."
                        ) from error
        return self.pool

    async def initialize(self) -> None:
        pool = await self.connect()
        try:
            async with pool.acquire() as connection:
                await connection.execute("SELECT pg_advisory_lock(724197)")
                try:
                    await connection.execute(SCHEMA)
                finally:
                    await connection.execute("SELECT pg_advisory_unlock(724197)")
        except asyncpg.PostgresError as error:
            raise LeashByteError(
                503, "run_store_unavailable", "The run store could not be prepared."
            ) from error

    async def ready(self) -> bool:
        try:
            pool = await self.connect()
            async with pool.acquire() as connection:
                await connection.fetchval("SELECT 1")
            return True
        except (LeashByteError, OSError, asyncpg.PostgresError, asyncio.TimeoutError):
            return False

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def create(self, run_id: str, request: RunCreateRequest, model: str) -> dict:
        pool = await self.connect()
        now = utcnow()
        policy = request.model_dump(mode="json")
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "navigations": 0}
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """INSERT INTO runs (
                    id, goal, state, created_at, provider, model, profile, start_url, policy, usage
                ) VALUES ($1,$2,'queued',$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)
                RETURNING *, 0::bigint AS step_count""",
                run_id,
                request.goal,
                now,
                request.provider,
                model,
                request.profile,
                request.start_url,
                json_value(policy),
                json_value(usage),
            )
        return public_run(row)

    async def get(self, run_id: str) -> dict:
        if RUN_ID.fullmatch(run_id) is None:
            raise LeashByteError(404, "run_not_found", "The run was not found.")
        row = await self._get_row(run_id)
        if row is None:
            raise LeashByteError(404, "run_not_found", "The run was not found.")
        return public_run(row, row["artifact_bytes"])

    async def _get_row(self, run_id: str, connection=None):
        pool = await self.connect()
        query = """SELECT r.*,
            (SELECT count(*) FROM run_steps s WHERE s.run_id=r.id) AS step_count,
            (SELECT coalesce(sum(size_bytes),0) FROM run_artifacts a WHERE a.run_id=r.id)
                AS artifact_bytes
            FROM runs r WHERE id=$1"""
        if connection is not None:
            return await connection.fetchrow(query, run_id)
        async with pool.acquire() as acquired:
            return await acquired.fetchrow(query, run_id)

    async def list_runs(self, limit: int, cursor: str | None) -> dict:
        pool = await self.connect()
        parameters: list[Any] = [limit + 1]
        condition = ""
        if cursor:
            try:
                cursor_date = datetime.fromisoformat(cursor.rsplit("/", 1)[0])
                cursor_id = cursor.rsplit("/", 1)[1]
                if RUN_ID.fullmatch(cursor_id) is None:
                    raise ValueError
            except (ValueError, IndexError) as error:
                raise LeashByteError(
                    422, "invalid_cursor", "The list cursor is not valid."
                ) from error
            condition = "WHERE (r.created_at, r.id) < ($2, $3)"
            parameters.extend([cursor_date, cursor_id])
        async with pool.acquire() as connection:
            query = f"""SELECT r.*,
                    (SELECT count(*) FROM run_steps s WHERE s.run_id=r.id) AS step_count,
                    (SELECT coalesce(sum(size_bytes),0) FROM run_artifacts a WHERE a.run_id=r.id)
                        AS artifact_bytes
                    FROM runs r {condition}
                    ORDER BY r.created_at DESC, r.id DESC LIMIT $1"""  # noqa: S608
            rows = await connection.fetch(query, *parameters)
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = f"{rows[-1]['created_at'].isoformat()}/{rows[-1]['id']}"
        return {
            "runs": [public_run(row, row["artifact_bytes"]) for row in rows],
            "next_cursor": next_cursor,
        }

    async def cancel(self, run_id: str, reason: str) -> dict:
        pool = await self.connect()
        async with pool.acquire() as connection, connection.transaction():
            locked = await connection.fetchrow("SELECT id FROM runs WHERE id=$1 FOR UPDATE", run_id)
            if locked is None:
                raise LeashByteError(404, "run_not_found", "The run was not found.")
            row = await self._get_row(run_id, connection)
            if row["state"] in {"succeeded", "failed", "cancelled", "limit_reached"}:
                return public_run(row, row["artifact_bytes"])
            if row["state"] == "queued":
                await connection.execute(
                    """UPDATE runs SET state='cancelled', cancel_requested=TRUE,
                        cancellation_reason=$2, finished_at=$3 WHERE id=$1 AND state='queued'""",
                    run_id,
                    reason,
                    utcnow(),
                )
            else:
                await connection.execute(
                    """UPDATE runs SET cancel_requested=TRUE, cancellation_reason=$2
                        WHERE id=$1 AND state='running'""",
                    run_id,
                    reason,
                )
            updated = await self._get_row(run_id, connection)
        return public_run(updated, updated["artifact_bytes"])

    async def claim(self) -> dict | None:
        pool = await self.connect()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """SELECT id FROM runs WHERE state='queued' AND cancel_requested=FALSE
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            if row is None:
                return None
            now = utcnow()
            await connection.execute(
                "UPDATE runs SET state='running', started_at=$2, heartbeat_at=$2 WHERE id=$1",
                row["id"],
                now,
            )
            claimed = await self._get_row(row["id"], connection)
        return public_run(claimed)

    async def raw(self, run_id: str) -> dict:
        pool = await self.connect()
        async with pool.acquire() as connection:
            row = await connection.fetchrow("SELECT * FROM runs WHERE id=$1", run_id)
        if row is None:
            raise LeashByteError(404, "run_not_found", "The run was not found.")
        return dict(row)

    async def heartbeat(self, run_id: str) -> None:
        pool = await self.connect()
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE runs SET heartbeat_at=$2 WHERE id=$1 AND state='running'", run_id, utcnow()
            )

    async def set_session(self, run_id: str, session_id: str) -> None:
        pool = await self.connect()
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE runs SET session_id=$2 WHERE id=$1 AND state='running'", run_id, session_id
            )

    async def update_usage(self, run_id: str, usage: dict) -> None:
        pool = await self.connect()
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE runs SET usage=$2::jsonb WHERE id=$1 AND state='running'",
                run_id,
                json_value(usage),
            )

    async def add_step(
        self,
        run_id: str,
        number: int,
        action: dict,
        before: dict,
        after: dict | None,
        error: str | None = None,
    ) -> None:
        pool = await self.connect()
        source_url = (after or before).get("url")
        async with pool.acquire() as connection:
            await connection.execute(
                """INSERT INTO run_steps (
                    run_id, number, snapshot_id, action, before_observation,
                    after_observation, source_url, error, created_at
                ) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7,$8,$9)""",
                run_id,
                number,
                before["snapshot_id"],
                json_value(action),
                json_value(before),
                json_value(after) if after is not None else None,
                source_url,
                error,
                utcnow(),
            )

    async def steps(self, run_id: str) -> list[dict]:
        await self.get(run_id)
        pool = await self.connect()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT * FROM run_steps WHERE run_id=$1 ORDER BY number", run_id
            )
        return [
            {
                "number": row["number"],
                "snapshot_id": row["snapshot_id"],
                "action": row["action"],
                "before_observation": row["before_observation"],
                "after_observation": row["after_observation"],
                "source_url": row["source_url"],
                "error": row["error"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]

    async def add_artifact(
        self, run_id: str, name: str, content_type: str, data: bytes, limit: int
    ) -> dict:
        pool = await self.connect()
        async with pool.acquire() as connection, connection.transaction():
            run = await connection.fetchrow("SELECT id FROM runs WHERE id=$1 FOR UPDATE", run_id)
            if run is None:
                raise LeashByteError(404, "run_not_found", "The run was not found.")
            used = await connection.fetchval(
                "SELECT coalesce(sum(size_bytes),0) FROM run_artifacts WHERE run_id=$1",
                run_id,
            )
            metadata = await self.artifacts.write(run_id, name, data, limit, used)
            try:
                await connection.execute(
                    """INSERT INTO run_artifacts (
                        run_id, name, content_type, size_bytes, sha256, created_at
                    ) VALUES ($1,$2,$3,$4,$5,$6)""",
                    run_id,
                    name,
                    content_type,
                    metadata["size_bytes"],
                    metadata["sha256"],
                    utcnow(),
                )
            except BaseException:
                self.artifacts.path(run_id, name).unlink(missing_ok=True)
                raise
        return metadata

    async def artifact(self, run_id: str, name: str) -> tuple[Path, str, str]:
        await self.get(run_id)
        pool = await self.connect()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM run_artifacts WHERE run_id=$1 AND name=$2", run_id, name
            )
        if row is None:
            raise LeashByteError(404, "artifact_not_found", "The artifact was not found.")
        path = self.artifacts.path(run_id, name)
        if not path.is_file():
            raise LeashByteError(404, "artifact_not_found", "The artifact was not found.")
        return path, row["content_type"], row["sha256"]

    async def finish(
        self,
        run_id: str,
        state: str,
        result: dict | None = None,
        error: str | None = None,
        cancellation_reason: str | None = None,
    ) -> None:
        if state not in {"succeeded", "failed", "cancelled", "limit_reached"}:
            raise ValueError("A terminal run state is required.")
        pool = await self.connect()
        async with pool.acquire() as connection:
            await connection.execute(
                """UPDATE runs SET state=$2, result=$3::jsonb, error=$4,
                    cancellation_reason=coalesce($5,cancellation_reason), finished_at=$6
                    WHERE id=$1 AND state='running'""",
                run_id,
                state,
                json_value(result) if result is not None else None,
                error,
                cancellation_reason,
                utcnow(),
            )

    async def delete(self, run_id: str) -> None:
        pool = await self.connect()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow("SELECT state FROM runs WHERE id=$1 FOR UPDATE", run_id)
            if row is None:
                raise LeashByteError(404, "run_not_found", "The run was not found.")
            if row["state"] not in {"succeeded", "failed", "cancelled", "limit_reached"}:
                raise LeashByteError(
                    409, "run_active", "Cancel the run and wait for it to stop before deletion."
                )
            await asyncio.to_thread(self.artifacts.delete, run_id)
            await connection.execute("DELETE FROM runs WHERE id=$1", run_id)
