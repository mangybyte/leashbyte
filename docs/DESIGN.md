# LeashByte design

This document describes how LeashByte is put together: the services, how requests flow through them, the agent loop, the vault, and the threat model behind the security choices. LeashByte is pre-alpha. The stack is meant to run on your own machine or network, and the PyPI and npm packages and the public site aren't out yet.

## What it is for

LeashByte is a self-hosted web toolkit for agents. A single `docker compose up` starts search, page fetch, browser sessions, agent runs and the MCP server, and one REST API (plus two SDKs) exposes all of it.

You bring the model. Any OpenAI-compatible endpoint works, so a local model can run agents without a provider bill. Claude runs go through the official Anthropic SDK and use `claude-haiku-5-5`.

The code is Apache-2.0, and we keep copyleft and other restricted code out of the repository so that stays simple for anyone building on it.

### Goals

- One way to install the whole system.
- Real Chrome for browser sessions.
- Small local models should be able to drive pages, which is why the agent works from an accessibility tree rather than screenshots.
- Credentials stay out of model prompts, model responses and logs.
- Internal network addresses are blocked by default.
- Every agent run leaves enough of a record that you can see what it did.
- You can plug in your own code, but we don't ship tools for getting around site defences.

### Non-goals

- A hosted, multi-tenant service.
- CAPTCHA solving or fingerprint faking in the core.
- Any promise that a given site allows automated access.
- A public deployment of the stack.

## Services

| Service | What it does |
| --- | --- |
| `api` | The REST API: authentication, search, fetch, validation and run queries. |
| `searxng` | Search, behind a pluggable search interface. It's the upstream image, unmodified. |
| `browser-pool` | Chrome lifecycle, profiles, browser actions and authenticated CDP access. |
| `vault` | Credential encryption and origin-bound secret retrieval. |
| `agent-worker` | Model calls, the agent loop and run cancellation. |
| `model-relay` | Forwards from the Compose subnet to a model endpoint on host loopback, and accepts nothing else. |
| `run-store` | PostgreSQL for run records, plus a local volume for artifacts. |
| `mcp` | MCP tools over stdio, or over streamable HTTP published on loopback, with the same authentication as the API. |

The API and the agent worker share the run store, and PostgreSQL doubles as the job queue so there's one less service to run. The API creates a run, a worker claims it with a row lock, and from then on the worker writes heartbeats and step records.

SearXNG lives in its own container and LeashByte talks to it over HTTP. None of its source is in this repository, which matters because SearXNG is AGPL (see the licence section below).

## Request paths

### Search

1. The API checks the API key and validates the query.
2. The configured search adapter sends the query to SearXNG.
3. The API returns results in a stable shape.

A search adapter implements one call, `search(query, limit, language, safe_search)`, and returns a title, URL and text excerpt for each result. SearXNG-specific fields never leak through, so a different backend can replace it without breaking clients.

### Fetch

1. The API checks the API key and validates the request.
2. The URL guard resolves and checks the destination.
3. The HTTP client downloads the response, within size and time limits.
4. The content processor turns it into Markdown, cleaned HTML or JSON.
5. The API returns the final URL, status, content type and content.

The URL guard checks every redirect and every new DNS answer, not just the first one. The fetch client ignores proxy environment variables, so a stray `HTTPS_PROXY` on the host can't route traffic around the guard.

### Browser session

1. The API asks the browser pool for a session.
2. The pool starts Chrome with an isolated profile.
3. The browser layer applies the network and credential policy.
4. The client sends actions or asks for an accessibility snapshot.
5. When the session ends, the pool closes Chrome and releases the profile.

Each session is held by a lease. When the lease expires, the browser stops. A session can't attach to a profile that another session is using.

### Agent run

1. The API validates the goal and creates a run record.
2. An agent worker claims the run.
3. The worker opens a browser session and takes a snapshot.
4. The model picks one structured action.
5. The browser layer validates the action and performs it.
6. The worker takes a new snapshot and stores the step.
7. The loop ends on completion, cancellation, an error or a run limit.

The worker checks for cancellation before every model call and every browser action, so a cancel only waits for the call or action already in progress.

## API surface

Every versioned endpoint needs an API key. The API binds to loopback by default, and anyone exposing it to another machine should put TLS and access control in front of it first.

### General

| Method and path | Result |
| --- | --- |
| `GET /healthz` | Process health. No authentication. |
| `GET /readyz` | Dependency readiness, with no sensitive detail. |
| `GET /v1/config` | Enabled features and public limits. |

### Search and fetch

| Method and path | Input or result |
| --- | --- |
| `GET /v1/search` | Query parameters `q`, `limit`, `language` and `safe_search`. |
| `POST /v1/fetch` | URL, output format, timeout and byte limit. |

Fetch results come back directly in the response. Storing them for later isn't implemented yet.

The fetch output formats are `markdown`, `html` and `json`. A JSON response comes back parsed. An HTML page can be converted to Markdown or cleaned HTML. Any other content type gets an `unsupported_content_type` error.

### Browser sessions

| Method and path | Input or result |
| --- | --- |
| `POST /v1/browser/sessions` | Profile name, start URL and lease duration. |
| `GET /v1/browser/sessions/{id}` | State, current URL, lease and capabilities. |
| `DELETE /v1/browser/sessions/{id}` | Closes the session and releases its resources. |
| `GET /v1/browser/sessions/{id}/snapshot` | The accessibility tree with numbered marks. |
| `POST /v1/browser/sessions/{id}/actions` | One validated browser action. |
| `GET /v1/browser/sessions/{id}/screenshot` | A PNG screenshot. |
| `GET /v1/browser/sessions/{id}/cdp` | Short-lived CDP connection details. |

The session request has a `proxy` field, but a custom proxy is rejected. All browser traffic goes through the browser's own guarded proxy, and letting a client swap it out would bypass the network checks.

CDP is a separate capability with its own key, and the endpoint hands out a short-lived credential. Chrome's debugging port is never exposed without authentication.

### Vault

| Method and path | Input or result |
| --- | --- |
| `POST /v1/vault/credentials` | Alias, user name, password and allowed origins. |
| `GET /v1/vault/credentials` | Aliases and allowed origins only. |
| `DELETE /v1/vault/credentials/{alias}` | Removes one credential. |
| `PUT /v1/vault/credentials/{alias}` | Replaces a secret or its allowed origins. |

No endpoint returns a stored password. Logs record aliases, never secret values.

### Agent runs

| Method and path | Input or result |
| --- | --- |
| `POST /v1/runs` | Goal, start URL, profile, provider and model, credential aliases, allowed origins and run limits. |
| `GET /v1/runs` | A cursor-paginated list of runs. |
| `GET /v1/runs/{id}` | State, progress, usage, result and error summary. |
| `GET /v1/runs/{id}/steps` | Ordered observations, actions and source URLs. |
| `GET /v1/runs/{id}/artifacts/{name}` | A screenshot, log or result artifact. |
| `POST /v1/runs/{id}/cancel` | Requests cancellation. |
| `DELETE /v1/runs/{id}` | Deletes a stopped run and its artifacts. |

Errors share one shape: `code`, `message` and `request_id`. Where it's relevant, the message names the destination host and HTTP status, but it never echoes credentials, full request headers or signed URLs.

Create endpoints don't deduplicate retries. List endpoints use cursor pagination, and every response carries a request ID.

## Agent loop

### Observation

The browser layer builds an accessibility snapshot and gives each actionable element a number. A mark refers to one element in one snapshot. The model receives:

- The user's goal and the current run limits.
- The current page origin and title.
- A compact accessibility tree with numbered marks.
- The last accepted actions and their results.
- Credential aliases, when the page origin is allowed for them.

The model doesn't get screenshots. A text-only local model has to be able to finish a run from the accessibility tree alone, and that is the case we design for.

Page text is untrusted. The prompt marks it as data, and it never becomes a system instruction.

### Action

The model returns one structured action from this set:

- `navigate`: open an allowed URL.
- `click`: activate one marked element.
- `type`: enter public text into one marked element.
- `fill_secret`: fill a credential placeholder into an allowed field.
- `select`: choose an option.
- `scroll`: move the viewport.
- `wait`: pause for a fixed, bounded interval.
- `done`: return a structured result and its source URLs.
- `fail`: explain why the goal can't be completed.

The worker rejects an unknown mark, a stale snapshot ID and anything outside the run's policy. Every accepted action produces a fresh observation before the next model call.

### Model interface

The local adapter talks to an OpenAI-compatible chat completions endpoint, and the Claude adapter uses the Anthropic SDK. Both require structured output for actions. The configuration holds the endpoint, model name, a reference to the API key and the generation limits. Logs record the model name and usage, never the key.

Local model servers usually listen on host loopback, and we didn't want people to open them up just to use LeashByte. So for a host-loopback model, the Compose relay binds only to the private Compose gateway, accepts connections only from the Compose subnet and forwards only to the configured host-loopback port.

Run limits cover steps, elapsed time, model tokens, navigations and artifact bytes. The worker stops at whichever it hits first.

## Credential vault

The vault encrypts each credential with authenticated encryption under its own random data key, and a master key from the environment wraps that data key. The master key also has a `_FILE` setting so it can come from a container secret.

The vault is built around these properties:

- A unique nonce for every encryption.
- The alias and allowed origins are bound in as authenticated metadata, so they can't be swapped between credentials.
- There's no default or hard-coded master key.
- No secret value appears in an API response, prompt, exception or log.
- Every credential has explicit origin grants.
- A fill only happens when the page's HTTPS origin exactly matches a grant.

The model only ever sees placeholders such as `{{credential:shop.username}}` and `{{credential:shop.password}}`. The browser layer resolves a placeholder after the policy checks pass and fills the field through the browser itself. The real value never enters the model conversation.

When the form is submitted, the allowed site receives the credential, which is what the fill is for. A different origin can't get the same fill without its own grant.

## Run records

Each run stores:

- Run ID, goal, state, timestamps and any cancellation reason.
- Model name, generation limits and token usage.
- Browser profile alias and session IDs.
- For each step: the step number, snapshot ID, action and redacted result.
- The current URL and source URLs for each step.
- Screenshot and result artifact references, with hashes.
- Application logs, with request IDs and secret redaction.
- The final structured output or a bounded error summary.

Artifacts live on a private volume, and downloading one needs the same authorization as the run it belongs to. You can delete a whole run through the API. There are no automatic retention settings yet.

## How polite the browser is

We settled on a polite core with plugin hooks.

The core uses real Chrome, honors robots.txt, applies per-host rate limits and supports Web Bot Auth so sites can identify it. It doesn't solve CAPTCHAs or fake browser fingerprints, and it doesn't accept a custom proxy for browser sessions.

The plugin interface is documented, and what an administrator chooses to load is up to them. LeashByte doesn't ship add-ons for fingerprinting or challenges, and the documentation doesn't promote getting around access controls.

Plugins are off by default. A plugin is trusted local code, and installing one is an administrator action. Nothing a page, a model or a remote repository does can install one.

## Threat model

### What we protect

- Vault credentials and model provider keys.
- The host and the user's internal network.
- Browser profiles and signed-in sessions.
- Run records, screenshots and page content.
- API keys and CDP credentials.
- Host CPU, memory, disk and network capacity.

### Trust boundaries

- API clients cross the API authentication boundary.
- Search results, pages, redirects and downloads are untrusted.
- Model output is untrusted until the worker has validated it.
- Browser content runs inside a restricted browser environment.
- The browser pool, vault and run store hold sensitive state.
- Plugins are trusted code supplied by the administrator.
- The SearXNG container is a separate upstream service.

### SSRF and internal network access

People will run LeashByte inside their own networks, next to things that were never meant to be reachable from the internet. So fetch and browser navigation deny these by default:

- Loopback, private, link-local, carrier-grade NAT and unique local addresses.
- Cloud metadata addresses and internal service names.
- Multicast, broadcast and unspecified addresses.
- Any scheme other than HTTP and HTTPS.
- URLs with credentials in them.

The guard resolves every IPv4 and IPv6 address for a host name and rejects the name if any one of them is denied. It repeats the check on every redirect and every new connection, and the connection layer pins the address it checked, which defeats DNS rebinding. An explicit allowlist can open up a named private destination.

The browser gets the same policy for navigation and subresources. Its traffic goes through a guarded proxy that applies the same deny rules, and Chrome has no direct route around it.

There are limits on response bytes, decompressed bytes, redirects, connection time, total time and concurrent requests. Error messages don't reveal the bodies of internal responses.

### Credential leakage

The vault and placeholder flow keep secrets away from the model. Filled values are redacted from observations and run records, and artifacts need authorization. A screenshot could show sensitive page data, so the browser refuses screenshots after a credential fill.

Origin checks stop a fill on a different site, and the subresource and frame policy stops a page from moving a protected field to another origin without a fresh check.

### Prompt injection

Page text and tool output stay in the untrusted part of the conversation. The agent's policy comes only from the user and the administrator, and every action is validated against fixed schemas and that policy.

A page can't add a credential grant, install a plugin, change the model endpoint or call an administrative API. Sensitive operations need an explicit user policy, and the run viewer shows where each action came from, so you can inspect anything that looks off.

### Browser and plugin compromise

Chrome has no host filesystem access beyond its profile and approved downloads. The container runs as a non-root user with a read-only root filesystem where possible, dropped capabilities and resource limits to soften denial of service.

Chrome's own sandbox is off, because it needs namespace operations the container doesn't permit. That leaves the container as the only isolation boundary, so a Chrome exploit would land inside it. [ROADMAP.md](ROADMAP.md) lists a review of that boundary as open work.

A plugin can run arbitrary code by design. Only an administrator can enable one, and the default deployment mounts no plugin directory.

### API, CDP and artifacts

The default listener is loopback. Remote access takes explicit configuration, TLS and strong authentication. CDP needs its own short-lived grant. The API never lists secret values, and artifact names resolve inside the run's own directory, so path traversal doesn't work.

### Security scanning

CI runs the tests and the dependency licence checks on every push to `main` and every pull request. GitHub secret scanning, with push protection, is on for the repository. See [SECURITY.md](../SECURITY.md).

## Licence review

We checked the licences of the projects in this space on 2026-11-08, mostly through the GitHub licence API.

| Project | Licence | What it means for this repo |
| --- | --- | --- |
| searxng/searxng | AGPL-3.0 | Used only as a separate, unmodified container that we call over HTTP. None of its code is here. |
| unclecode/crawl4ai | Apache-2.0 | Fine as a library, keeping its NOTICE. |
| mendableai/firecrawl (now firecrawl/firecrawl) | AGPL-3.0 | Not used, and no code copied. |
| steel-dev/steel-browser | Apache-2.0 | Compatible. |
| browserless/browserless | GitHub reports NOASSERTION ("Other"). Our reading of its LICENSE file is SSPL-1.0 or a commercial licence. | Not used, and none of its code is here. |
| daijro/camoufox | MPL-2.0 | Could only ever be an optional, separate browser. Changes to its own files would stay MPL-2.0. |
| Kaliiiiiiiiii-Vinyzu/patchright | Apache-2.0 | Fine as a library. |
| browser-use/browser-use | MIT | Compatible. |
| browserbase/stagehand | MIT | Compatible. |
| Skyvern-AI/skyvern | AGPL-3.0 | Not used. Nothing copied from it. |
| adbar/trafilatura | Apache-2.0 | Fine as a library. |

The rule that falls out of this: the repository only holds Apache-2.0-compatible code (Apache-2.0, MIT), and AGPL, SSPL and MPL projects stay outside it. SearXNG is bundled only as its own unchanged container image behind the pluggable search interface.

Before a new library goes in, we check its licence again, along with its transitive dependencies and any NOTICE requirements.

### Shipped dependencies

The licence inventory covers the locked Linux runtime Python environment and all the JavaScript development dependencies. It fails on an unknown, AGPL or SSPL licence, and the reports list every package and version. Container images keep their upstream copyright and licence files.

Two Python packages fall outside the Apache-2.0 and MIT set. `certifi` is MPL-2.0, and `tld` offers MPL-1.1, GPL-2.0-only or LGPL-2.1-or-later. Both are unmodified external packages and none of their source is copied into the repository, but MPL doesn't fit the strict wording of the rule above. We haven't yet decided whether to replace them or record an exception, and the item is open in [ROADMAP.md](ROADMAP.md).

SearXNG is the only AGPL component. Compose runs the pinned upstream image unchanged and calls it over HTTP, and its licence and source offer stay with that service.

Playwright keeps its Microsoft and Puppeteer notices, and the Anthropic package keeps its vendored `httpx-aiohttp` notice. Google Chrome is a proprietary browser installed by the browser image. [THIRD_PARTY.md](THIRD_PARTY.md) has the image references and where each notice lives.

## Browser design notes

The browser pool gives each session its own profile and an explicit lease, the agent works in an observe-act-observe loop, and every browser and profile is released when its session ends. There's no API for copying login state from one profile to another. If one is ever added, it should be an explicit export and import step.
