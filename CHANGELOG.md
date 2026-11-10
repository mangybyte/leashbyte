# Changelog

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
