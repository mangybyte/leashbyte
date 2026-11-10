"""Run one fetch, one search and one local-model agent run."""

import argparse
import json
import os

from leashbyte_sdk import FetchRequest, LeashByteClient, RunCreateRequest, RunLimits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://example.com/")
    parser.add_argument("--model", default=os.environ.get("LEASHBYTE_LOCAL_MODEL_NAME"))
    parser.add_argument("--timeout-seconds", type=float, default=600)
    args = parser.parse_args()
    if not args.model:
        parser.error("Set LEASHBYTE_LOCAL_MODEL_NAME or pass --model.")
    base_url = os.environ.get(
        "LEASHBYTE_API_URL", f"http://127.0.0.1:{os.environ.get('LEASHBYTE_PORT', '8080')}"
    )
    with LeashByteClient(base_url=base_url) as client:
        page = client.fetch(FetchRequest(url=args.url))
        print(f"Fetch: {page.status} {page.final_url}")
        results = client.search("self-hosted web automation", limit=3)
        print(f"Search: {len(results.results)} results")
        run = client.create_run(
            RunCreateRequest(
                goal="Read the page. Return its exact title and source URL as the result.",
                start_url=args.url,
                provider="local",
                model=args.model,
                limits=RunLimits(
                    steps=8,
                    elapsed_seconds=int(args.timeout_seconds),
                    tokens=12_000,
                    navigations=2,
                    artifact_bytes=5_000_000,
                ),
            )
        )
        print(f"Run: {run.id}")
        finished = client.wait_for_run(run.id, timeout_seconds=args.timeout_seconds + 30)
        print(f"State: {finished.state}")
        print(json.dumps(finished.result or {"error": finished.error}, indent=2))
        if finished.state != "succeeded":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
