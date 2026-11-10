"""Container entry point for the single agent worker process."""

import asyncio

from .agent_worker import AgentWorker
from .config import Settings
from .run_store import ArtifactStore, RunStore


def main() -> None:
    settings = Settings()
    store = RunStore(
        settings.database_dsn.get_secret_value(), ArtifactStore(settings.run_artifact_root)
    )
    asyncio.run(AgentWorker(settings, store).run())


if __name__ == "__main__":
    main()
