# Known limits and next steps

LeashByte is pre-alpha. These are the gaps we know about and the work we plan to do next.

## SDKs and API

- MCP artifact results can be large. They should get a smaller size limit or return a download pointer instead.
- The Python and TypeScript SDKs treat an explicitly empty API key differently. They should agree.
- The TypeScript live check for listing runs can race with run deletion and needs a stable version.
- The API error schema and the design document disagree about which error fields are optional.
- Cache and cursor handling needs a review for malformed or stale inputs.

## Browser and credentials

- The credential-fill form check can be hardened against DOM clobbering, submit-button overrides and scripts that change the form after a fill.
- The requested-upgrade path in the browser relay needs a review. The normal non-CONNECT path doesn't use it, and WebSockets go through CONNECT.
- The Chrome container boundary needs a review, since Chrome's own sandbox is off (the container forbids the namespace operations it needs).
- The agent worker's access to the vault list could be narrower.
- The SDKs could do more to push you towards HTTPS, through safer defaults or warnings.

## Runtime and configuration

- The SearXNG secret setting isn't validated yet, so the example placeholder can end up in a live stack.
- If a model server listens on all interfaces at the relay port, the relay can fail to bind while its health check still passes. We should detect that or at least document it.
- Host-side tests can pick up settings from a local `.env` and should be isolated from it.

## Dependencies and releases

- `certifi` (MPL-2.0) and `tld` (MPL-1.1, GPL-2.0-only or LGPL-2.1-or-later) fall outside our licence policy. We still need to decide whether to replace them or keep them as recorded exceptions.
- The static site has no dependency licence inventory yet.
- Third-party GitHub Actions and container images should be pinned by digest.
- Mail to the leashbyte.com addresses hasn't been verified end to end yet.
