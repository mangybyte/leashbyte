# Contributing to LeashByte

LeashByte is pre-alpha and the API and security boundaries are still settling, so please open an issue before you start on anything substantial. It saves you writing a change that clashes with something already planned.

To report a bug or suggest a feature, [open an issue](https://github.com/mangybyte/leashbyte/issues/new/choose) and pick the matching form. When you open a pull request, the description starts from a short checklist. Fill it in rather than deleting it: it answers the questions a reviewer would ask first.

## Running the checks

You'll need Docker with the Compose plugin, Python 3.13 with uv 0.13.4, and Node 20 or later (22.12 or later if you build the site). From the repository root:

```bash
uv sync --locked
uv run ruff check src tests sdk/python/src examples ci/mcp_roundtrip.py scripts/python_licenses.py
uv run ruff format --check src tests sdk/python/src examples ci/mcp_roundtrip.py scripts/python_licenses.py
uv run python scripts/python_licenses.py --check
uv run pytest -q tests
npm ci --prefix sdk/typescript
npm --prefix sdk/typescript test
npm --prefix sdk/typescript run licenses
```

CI runs the same checks on every push to `main` and every pull request, builds the static site and starts the Compose stack for a smoke test. If you change a route's contract, regenerate `openapi.json` and the SDK models as the [README](README.md#development) describes.

## What a good pull request looks like

- It does one thing. Unrelated formatting changes and refactors make a change harder to review, so send those separately.
- The description says what changed, how you checked it and anything it doesn't cover yet. Link the issue it relates to.
- It comes with tests where they matter most: security boundaries, parsers of untrusted input and the fix for a bug, so the bug stays fixed.
- It fits the architecture in [docs/DESIGN.md](docs/DESIGN.md). If you think the design should change, say so in the issue first.
- It contains no credentials, private data, internal host names or internal paths, not even in test fixtures.

## How review works

A maintainer reads every pull request, and we don't merge one until CI passes on it.

Small, focused changes get reviewed faster. A pull request that mixes a fix with a refactor or a new feature usually goes back to be split.

Some parts of the code are where a mistake turns into a security hole, so a change to any of them gets a deeper review and takes longer:

- the vault (`vault.py`, `vault_service.py`), which holds secrets the model must never see
- browser isolation and CDP access (`browser.py`, `browser_service.py`)
- the fetch guard that keeps fetch and browser traffic away from loopback, private and metadata addresses (`url_guard.py`, `fetch.py`, `proxy.py`)
- authentication: the API key checks in the REST API and the MCP server, the separate CDP capability key, and the service keys between internal services (`main.py`, `internal.py`, `mcp/server.py`)

On a pull request from outside the project, CI doesn't start until a maintainer has read the change and approved the run. That applies to every outside pull request, so it's not a judgment on yours.

We can't promise a review date. If a change doesn't fit the project, we'll close the pull request and say why, so you're not left waiting.

## Using AI tools

You're welcome to use AI coding tools. The bar is the same as for any other change: you understand everything you send, you've run it and tested it yourself, and you can answer questions about it in review. You're responsible for the change, whatever wrote the first draft.

A pull request that looks generated and untested, for example code that doesn't run, tests that don't test the change, or a description that doesn't match the diff, will be closed.

## New dependencies

LeashByte is Apache-2.0, and we want anyone to be able to use, modify and redistribute it under those terms without picking up extra obligations. So new code and dependencies need Apache-2.0-compatible licences, such as Apache-2.0 or MIT. AGPL, SSPL and MPL projects stay outside the repository. The licence checks above fail on anything unknown, and when you add a dependency, please keep any copyright and NOTICE files it requires.

## Reporting a vulnerability

Please don't use a public issue or pull request for this. Use the [private report form](https://github.com/mangybyte/leashbyte/security/advisories/new) or email `security@leashbyte.com`. [SECURITY.md](SECURITY.md) has the details.

## Licence and conduct

By submitting a contribution, you agree that the project may distribute it under Apache-2.0. The [Contributor Covenant](CODE_OF_CONDUCT.md) applies in all project spaces and when you represent the project in public.
