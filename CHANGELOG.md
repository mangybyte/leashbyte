# Changelog

## 0.1.2 - 2026-11-10

Agents can now read the page they are on.

### Added

- Snapshots and agent observations carry the rendered text of the page in reading order, as `text`, cut at 4,000 characters, with `text_truncated` set when we cut it. Secret values are redacted from it the same way as element names. Before, the model only saw the title and the names of links, buttons and form fields, so it could not answer a question about a paragraph or a price.

### Changed

- The agent prompt tells the model to answer only from the observation and to fail when the answer isn't there, rather than guess.
- The schema we send to Claude now states the numeric and length limits in the field descriptions, since the API doesn't accept them as constraints. Waits and scroll offsets outside their limits are clamped instead of failing the run.
- When a model reply fails validation, the run error now says which check failed (for example `string_too_long at result`), without any of the reply text.
- An Anthropic key file that the worker can't read now gives a clear run error and a warning at start. The README explains the access the file needs.
- Element names are redacted before they are cut, so a secret can't show up in part at the cut.
- README links point at the release tag, so the PyPI page doesn't change under you.

## 0.1.1 - 2026-11-10

The first release on PyPI, as `leashbyte` and `leashbyte-sdk`. 0.1.0 was a GitHub-only release and was never uploaded to a package registry. There are no code changes.

### Changed

- The READMEs and the project site now give the PyPI install lines.
- `leashbyte` now requires `leashbyte-sdk>=0.1.1,<0.2`. Before, it accepted any version.
- The TypeScript SDK is on npm as `leashbyte` 0.1.1. Its code is the same as the `v0.1.1` tag. We published it from a later commit that only changes the docs, so the README in the package gives the npm install line.

## 0.1.0 - 2026-11-10

First public source release.

### Added

- Web search through a separate SearXNG service.
- Page fetch with Markdown, cleaned HTML and parsed JSON output, with time, size and redirect limits.
- Destination checks on fetch and browser traffic, so neither can reach internal addresses by default.
- Isolated Chrome sessions with named profiles, page actions and short-lived CDP access.
- An encrypted credential vault that only fills a credential on the exact origins it's granted to.
- Goal-driven agent runs with limits, cancellation, run records and artifacts.
- A local run viewer.
- An MCP server with stable search, fetch, browser and run tools.
- Python and TypeScript SDKs.
- A Docker Compose stack for local use, and a static project site.
- API key authentication, a security policy and Apache-2.0 licensing.

### Notes

LeashByte is pre-alpha. Install it from GitHub and run it with Docker Compose. The PyPI and npm packages aren't published yet and the project site isn't deployed. [Known limits and next steps](docs/ROADMAP.md) lists what's still open.
