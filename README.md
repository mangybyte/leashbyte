# LeashByte

LeashByte is a self-hosted toolkit that gives AI agents access to the web. It puts search, page fetching, real Chrome sessions, an encrypted credential vault and goal-driven agent runs behind one REST API, with an MCP server and Python and TypeScript SDKs on top. It's meant for developers who want their agents to search, read and use websites from their own machines, driven by a model they choose. A local model works fine.

LeashByte is pre-alpha, so expect rough edges and breaking changes. The services run with Docker Compose from a source checkout. The Python SDK is on PyPI as `leashbyte-sdk`, and the server package as `leashbyte`. The TypeScript SDK is on npm as `leashbyte`.

The project home is [leashbyte.com](https://leashbyte.com).

## Getting started

### Requirements

- Docker with the Compose plugin.
- Free local TCP ports: 8080 for the API and 8081 for MCP by default.
- About 2 GB of free memory for the browser pool.
- An OpenAI-compatible chat completions endpoint, if you want agent runs on a local model.
- Python 3.13 and uv 0.13.4 for the quickstart, the Python SDK and stdio MCP.
- Node 20 or later for the TypeScript SDK.

### Get the source

```bash
git clone https://github.com/mangybyte/leashbyte.git
cd leashbyte
```

The Compose stack builds from this checkout, so start here even if you plan to use the packages. If all you need is a Python client for a stack that's already running, `pip install leashbyte-sdk` is enough.

### Start the services

1. Copy the example configuration.

   ```bash
   cp .env.example .env
   ```

2. `.env` has eight secret placeholders. Generate a separate random value for each one and paste it in.

   ```bash
   openssl rand -hex 32
   ```

3. If something else already uses port 8080, change `LEASHBYTE_PORT`.

4. Start everything.

   ```bash
   docker compose up --build -d --wait
   ```

Only the API and the MCP server get host ports, and both bind to `127.0.0.1` by default. SearXNG, the browser pool, the vault, the agent worker and PostgreSQL stay on the internal Compose network with no host ports. Leave the bind addresses as they are unless you've put TLS and access control in front of them.

The browser image installs Google Chrome during the first build. Chrome runs as a non-root user inside the browser container, and every session gets its own profile directory and a time lease.

Chrome's own sandbox is turned off, because it needs namespace operations that the container doesn't allow. The container is the isolation boundary instead: it has a read-only root filesystem, dropped capabilities and resource limits.

## Quickstart

The quickstart script does one fetch, one search and one agent run against a local model, so start an OpenAI-compatible model server first. Then set two values in `.env`:

- `LEASHBYTE_LOCAL_MODEL_NAME` is the model name your server exposes.
- `LEASHBYTE_LOCAL_MODEL_PORT` is the port the server listens on, on host loopback. The default is 11434, which is what Ollama uses. vLLM commonly uses 8000 and LM Studio 1234. If your llama-server is on port 8080, change `LEASHBYTE_PORT` as well so the two don't collide.

The model relay uses Docker host networking, which the supplied Compose configuration supports on Linux. Keep your model bound to host loopback. The relay makes it reachable from the LeashByte Compose subnet and nowhere else.

Load the configuration and install the locked Python dependencies:

```bash
set -a
source .env
set +a
uv sync --locked
```

Run the example:

```bash
uv run python examples/quickstart.py
```

`--url` picks a different public page and `--model` overrides `LEASHBYTE_LOCAL_MODEL_NAME`. The script exits non-zero if the agent run doesn't succeed. You don't need an Anthropic key for any of this.

## MCP server

Compose runs the streamable HTTP MCP server at `http://127.0.0.1:8081/mcp`. It takes the same `X-API-Key` header as the REST API and returns HTTP 401 if the key is missing or wrong. If you change `LEASHBYTE_MCP_BIND_ADDRESS`, add TLS and access control at the same time.

For stdio, run `uv sync --locked` and use a client configuration like this one, with both example paths replaced:

```json
{
  "mcpServers": {
    "leashbyte": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/leashbyte", "leashbyte-mcp"],
      "env": {
        "LEASHBYTE_API_URL": "http://127.0.0.1:8080",
        "LEASHBYTE_API_KEY_FILE": "/secure/path/leashbyte-api-key"
      }
    }
  }
}
```

The MCP tools cover search, fetch, browser sessions and runs. Vault management and CDP are left out on purpose: they're operator tasks, and the thing on the other end of an MCP connection is usually a model. To log in to a site, use `fill_secret` with a credential placeholder. Anything a page returns is untrusted data.

If you'd rather not point at a checkout, `pip install leashbyte` puts the `leashbyte-mcp` command on your PATH. Use `"command": "leashbyte-mcp"` with no `args` and the same `env`. It only talks to the REST API, so the Compose stack still has to be running.

`LEASHBYTE_API_KEY_FILE` is for stdio MCP and the SDKs running on the host. The Compose MCP service uses `LEASHBYTE_API_KEY` and doesn't mount the host key file.

## Python SDK

`leashbyte-sdk` lives in `sdk/python` and covers the stable REST API. Install it from PyPI:

```bash
pip install leashbyte-sdk
```

In a checkout, `uv sync --locked` installs it for you. The Pydantic models are generated from `openapi.json`, and a hand-written client on top handles authentication, binary downloads and run polling.

```python
from leashbyte_sdk import FetchRequest, LeashByteClient

with LeashByteClient() as client:
    page = client.fetch(FetchRequest(url="https://example.com/"))
    print(page.status, page.final_url)
```

The client reads `LEASHBYTE_API_KEY` or `LEASHBYTE_API_KEY_FILE`. It doesn't read `LEASHBYTE_API_URL`, so pass `base_url` if the API isn't at the default `http://127.0.0.1:8080`.

The client keeps its configured key out of `repr` and API errors. It can't do the same for your own logging, so keep request bodies, screenshots, page content and run artifacts out of your logs. There's a run example in [`sdk/python/README.md`](https://github.com/mangybyte/leashbyte/blob/v0.1.2/sdk/python/README.md).

## TypeScript SDK

The TypeScript SDK is on npm. It has no runtime npm dependencies and uses Node's built-in `fetch` API.

```bash
npm install leashbyte
```

```typescript
import { LeashByteClient } from "leashbyte";

const client = new LeashByteClient();
const page = await client.fetchPage({ url: "https://example.com/" });
console.log(page.status, page.final_url);
```

The source lives in `sdk/typescript`. Its [README](https://github.com/mangybyte/leashbyte/blob/v0.1.2/sdk/typescript/README.md) has more usage details and shows how to build it from the checkout.

The client reads `LEASHBYTE_API_KEY` or `LEASHBYTE_API_KEY_FILE`. Pass `baseUrl` to the constructor for a different API address. The configured key is redacted from inspection, JSON serialization and API errors.

## Using the API

Load the local key without printing it:

```bash
set -a
source .env
set +a
BASE="http://127.0.0.1:${LEASHBYTE_PORT}"
AUTH="X-API-Key: ${LEASHBYTE_API_KEY}"
```

Check process health and dependency readiness:

```bash
curl --fail-with-body "$BASE/healthz"
curl --fail-with-body "$BASE/readyz"
```

Those two are open. The versioned endpoints all need the `X-API-Key` header and return HTTP 401 if it's missing or wrong.

### Search and fetch

Search:

```bash
curl --fail-with-body -H "$AUTH" \
  "$BASE/v1/search?q=self-hosted%20search&limit=3&language=en&safe_search=1"
```

Fetch a page as Markdown:

```bash
curl --fail-with-body -H "$AUTH" -H 'Content-Type: application/json' \
  --data '{"url":"https://example.com/","format":"markdown"}' \
  "$BASE/v1/fetch"
```

`"format":"html"` gives you cleaned HTML instead. Use `"format":"json"` when the destination returns JSON, and you get the parsed value back.

### Browser sessions

Create a session on a named profile:

```bash
curl --fail-with-body -H "$AUTH" -H 'Content-Type: application/json' \
  --data '{"profile":"research","start_url":"https://example.com/","lease_seconds":900}' \
  "$BASE/v1/browser/sessions"
```

You get back a session ID and the lease expiry. The other session endpoints take that ID:

```bash
curl --fail-with-body -H "$AUTH" "$BASE/v1/browser/sessions/SESSION_ID"
curl --fail-with-body -H "$AUTH" "$BASE/v1/browser/sessions/SESSION_ID/snapshot"
curl --fail-with-body -H "$AUTH" "$BASE/v1/browser/sessions/SESSION_ID/screenshot" --output page.png
curl --fail-with-body -X DELETE -H "$AUTH" "$BASE/v1/browser/sessions/SESSION_ID"
```

A snapshot gives each element you can act on a numbered mark, and it never includes input values. An action names a mark in a particular snapshot, so send the current snapshot ID with it:

```bash
curl --fail-with-body -H "$AUTH" -H 'Content-Type: application/json' \
  --data '{"action":"click","snapshot_id":"SNAPSHOT_ID","mark":1}' \
  "$BASE/v1/browser/sessions/SESSION_ID/actions"
```

The actions are `navigate`, `click`, `type`, `select`, `scroll` and `fill_secret`. Every accepted action returns a new observation with a new snapshot ID, and the next action has to use that one. Tying each action to the snapshot it was based on means you can't click a mark from a page that has since changed.

Closing a session stops Chrome and releases the profile, and so does letting the lease run out. Only one active session can use a profile at a time.

### CDP access

For raw Chrome DevTools Protocol access you need the API key and the separate `LEASHBYTE_CDP_KEY` value:

```bash
curl --fail-with-body -H "$AUTH" -H "X-CDP-Key: ${LEASHBYTE_CDP_KEY}" \
  "$BASE/v1/browser/sessions/SESSION_ID/cdp"
```

The response has a short-lived token and a WebSocket path. Connect with the token in the `X-CDP-Grant` header and the API key in `X-API-Key`. A grant works once and only for the session it was issued for. Chrome itself has no public debugging port.

The relay only connects to the current page, and it denies browser, target, storage, interception, file-input and download methods. A session can use CDP or credential fills but not both, because raw CDP could read a filled value straight out of the page.

Named profiles keep cookies and local storage between sessions, so a later CDP session on the same profile can read the page data an earlier one left behind. Give each trust domain its own profile.

### Credential vault

Add a credential and grant it to exact HTTPS origins:

```bash
curl --fail-with-body -H "$AUTH" -H 'Content-Type: application/json' \
  --data '{"alias":"shop","username":"USER_NAME","password":"PASSWORD","allowed_origins":["https://shop.example"]}' \
  "$BASE/v1/vault/credentials"
```

The user name and password are encrypted with a random data key, and the master key from your environment wraps that data key. Every write uses new nonces, and the vault file never contains a plaintext credential.

List and delete credentials like this. A `PUT` to the same path as the delete replaces a credential.

```bash
curl --fail-with-body -H "$AUTH" "$BASE/v1/vault/credentials"
curl --fail-with-body -X DELETE -H "$AUTH" "$BASE/v1/vault/credentials/shop"
```

Listing returns aliases and allowed origins only. No endpoint ever returns a stored password.

To use a credential, fill it through a browser action:

```json
{
  "action": "fill_secret",
  "snapshot_id": "SNAPSHOT_ID",
  "mark": 2,
  "placeholder": "{{credential:shop.password}}"
}
```

The page's current origin has to match one of the credential's allowed HTTPS origins exactly. The browser service then gets the secret from the private vault service and fills the field itself, so the value never shows up in the API response, the observation or the logs. Use `shop.username` for the user name field. After a fill, screenshots are turned off for the rest of that session, since the page may now be showing something sensitive.

### Agent runs

For local models the worker uses an OpenAI-compatible chat completions endpoint. Your model server can stay bound to host loopback: a private relay reaches it there, so you don't have to change its bind address. The relay listens only on the LeashByte Compose gateway and only accepts connections from the Compose subnet. If that subnet is already in use on your machine, change `LEASHBYTE_COMPOSE_SUBNET` and `LEASHBYTE_COMPOSE_GATEWAY` together. To skip the relay, set `LEASHBYTE_LOCAL_MODEL_BASE_URL` to a different address.

A host firewall has to let TCP through from the Compose subnet to the gateway on the relay port. With UFW on its default settings, that's:

```bash
sudo ufw allow from 172.29.250.0/24 to 172.29.250.1 port 11434 proto tcp comment 'LeashByte model relay'
```

Change the port in the rule if you've changed `LEASHBYTE_LOCAL_MODEL_PORT`. The rule doesn't change the model's own listener, and other networks still can't reach the relay.

Claude runs use `claude-haiku-5-5` through the official Anthropic SDK. Put an API key in a file outside the repository and point `LEASHBYTE_ANTHROPIC_API_KEY_HOST_FILE` at it, and Compose mounts it read-only at `/run/anthropic.key` in the agent worker.

The worker runs as uid 10001 with every Linux capability dropped, and a bind mount keeps the host file's owner and mode. So a key file with mode `0600` that belongs to your own user can't be read inside the container. Give uid 10001 read access and keep everyone else out. On Linux, an ACL entry does that without changing the owner:

```bash
KEY_FILE=/path/to/anthropic.key
chmod 600 "$KEY_FILE"
setfacl -m u:10001:r--,g::--- "$KEY_FILE"
```

A later `chmod` that gives the group nothing also resets the ACL mask and takes the read entry away again, so run `setfacl` after it. With rootless Docker or Podman, uid 10001 in the container maps to a different uid on the host, so give that uid access instead. If you don't have `setfacl`, `sudo chown 10001 "$KEY_FILE"` with mode `0400` works too. When the worker can't read the key, it logs `Claude runs are unavailable` at start, and every Claude run fails with an error that names `LEASHBYTE_ANTHROPIC_API_KEY_FILE` and the reason, such as `Permission denied`.

Create a run with fixed limits:

```bash
curl --fail-with-body -H "$AUTH" -H 'Content-Type: application/json' \
  --data '{"goal":"Open the page and report its title","start_url":"https://example.com/","provider":"local","limits":{"steps":12,"elapsed_seconds":600,"tokens":12000,"navigations":5,"artifact_bytes":5000000}}' \
  "$BASE/v1/runs"
```

Look at a run and its accepted steps:

```bash
curl --fail-with-body -H "$AUTH" "$BASE/v1/runs/RUN_ID"
curl --fail-with-body -H "$AUTH" "$BASE/v1/runs/RUN_ID/steps"
curl --fail-with-body -H "$AUTH" "$BASE/v1/runs?limit=50"
```

The worker works from the accessibility tree and sends no images to the model. For each run it records the actions, observations, source URLs, token usage, screenshots and a run log. The browser stops taking screenshots after a credential fill. Downloading an artifact needs the same API key as the run.

Cancel a run that's still going:

```bash
curl --fail-with-body -H "$AUTH" -H 'Content-Type: application/json' \
  --data '{"reason":"The user stopped this run."}' \
  "$BASE/v1/runs/RUN_ID/cancel"
```

Delete a run that has stopped, along with its artifacts:

```bash
curl --fail-with-body -X DELETE -H "$AUTH" "$BASE/v1/runs/RUN_ID"
```

There's a local run viewer at `http://127.0.0.1:8080/runs`. Enter the API key on the page. It doesn't store the key.

## Network controls

A service that fetches arbitrary URLs from inside your network is an easy SSRF target, so LeashByte is strict about where it connects. The URL guard allows HTTP and HTTPS only, and it rejects URLs with credentials in them, internal host names and non-public IP addresses. It checks every DNS answer and every redirect, and the fetch connection then goes only to the addresses it checked. That last part is what stops DNS rebinding.

Browser navigation and subresource requests follow the same rules. A loopback proxy checks each destination and connects only to the checked IP addresses, including for HTTPS and WebSocket tunnels. Service workers are blocked. Navigation also respects robots.txt and per-host rate limits.

To reach something private, add an exact host-and-port entry to `LEASHBYTE_PRIVATE_NETWORK_ALLOWLIST`. Separate entries with commas.

```dotenv
LEASHBYTE_PRIVATE_NETWORK_ALLOWLIST=internal.example:8080,[fd00::10]:8443
```

An entry allows private addresses for that one destination and nothing else. If you don't need private access, leave the list empty.

`LEASHBYTE_BROWSER_TLS_SPKI_ALLOWLIST` exists for controlled test fixtures. Chrome trusts every key in it for every host name, so leave it empty whenever real credentials are involved.

Responses have time, byte, redirect and concurrency limits. Going over one gets you a bounded error, and error responses never include the body the destination sent back.

## Web Bot Auth

LeashByte can sign its browser requests with Ed25519 HTTP message signatures (Web Bot Auth), which lets a site check who is crawling it. To turn it on, set:

```dotenv
LEASHBYTE_WEB_BOT_AUTH_HOST_KEY_FILE=/secure/path/web-bot-auth.pem
LEASHBYTE_WEB_BOT_AUTH_KEY_FILE=/run/web-bot-auth.key
LEASHBYTE_WEB_BOT_AUTH_KEY_ID=your-key-id
```

Keep the private key outside the repository. Compose mounts it read-only. With signing on, the browser adds `Signature-Agent`, `Signature-Input` and `Signature` headers to its HTTP requests.

## Plugins

Plugins are off by default. The core aims to be a well-behaved client, so beyond the checks above it offers one narrow hook for request headers. LeashByte ships no CAPTCHA solver, fingerprint add-on or evasion plugin.

A plugin is a Python module that a trusted administrator installs inside the browser container. Mount it read-only, put its directory on `PYTHONPATH`, then set:

```dotenv
LEASHBYTE_BROWSER_PLUGINS_ENABLED=true
LEASHBYTE_BROWSER_PLUGIN_MODULES=example_plugin
```

Separate module names with commas. They load in list order. A module can define this synchronous hook:

```python
def before_request(*, headers: dict[str, str], url: str, method: str) -> dict[str, str]:
    updated = dict(headers)
    updated["X-Operator-Contact"] = "https://leashbyte.com"
    return updated
```

The hook runs after the URL, network, robots and rate-limit checks. It receives the complete request headers and has to return the complete header dictionary. If the hook raises an error, the request is blocked. Plugins are trusted code and run with the browser container's permissions, so only enable code you've reviewed.

## Configuration

[`.env.example`](https://github.com/mangybyte/leashbyte/blob/v0.1.2/.env.example) has the main settings. Every application secret also has a `_FILE` variant for container secrets. The API won't start without an API key, and the vault won't start without a 32-byte master key.

The OpenAPI schema is in [`openapi.json`](https://github.com/mangybyte/leashbyte/blob/v0.1.2/openapi.json).

## Stopping the services

```bash
docker compose down
```

The profile, vault and SearXNG volumes are kept. Add `--volumes` only if you want that data deleted.

## Development

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

If you change a route's contract, regenerate the API schema and then the SDK models, and look over the diff:

```bash
uv run python scripts/generate_openapi.py
uvx --from datamodel-code-generator==0.91.0 datamodel-codegen \
  --input openapi.json --input-file-type openapi \
  --output sdk/python/src/leashbyte_sdk/_models.py \
  --output-model-type pydantic_v2.BaseModel --use-standard-collections \
  --use-annotated --enum-field-as-literal all --target-python-version 3.13 \
  --use-schema-description --use-field-description --disable-timestamp
npm --prefix sdk/typescript run generate
```

The GitHub Actions workflow runs the same checks. It also starts the Compose stack against a controlled local fixture and makes one authenticated MCP call. CI doesn't deploy anything.

## Documents

- [Architecture and threat model](https://github.com/mangybyte/leashbyte/blob/v0.1.2/docs/DESIGN.md)
- [Known limits and next steps](https://github.com/mangybyte/leashbyte/blob/v0.1.2/docs/ROADMAP.md)
- [Changelog](https://github.com/mangybyte/leashbyte/blob/v0.1.2/CHANGELOG.md)
- [Third-party licences](https://github.com/mangybyte/leashbyte/blob/v0.1.2/docs/THIRD_PARTY.md)
- [Python licence inventory](https://github.com/mangybyte/leashbyte/blob/v0.1.2/docs/python-licenses.json)
- [JavaScript licence inventory](https://github.com/mangybyte/leashbyte/blob/v0.1.2/sdk/typescript/THIRD_PARTY_LICENSES.json)
- [Security policy](https://github.com/mangybyte/leashbyte/blob/v0.1.2/SECURITY.md)
- [Contributing](https://github.com/mangybyte/leashbyte/blob/v0.1.2/CONTRIBUTING.md)

## Licence

LeashByte is licensed under the [Apache License 2.0](https://github.com/mangybyte/leashbyte/blob/v0.1.2/LICENSE). Copyright 2026 MangyByte Ltd. See [NOTICE](https://github.com/mangybyte/leashbyte/blob/v0.1.2/NOTICE) and the [third-party licences](https://github.com/mangybyte/leashbyte/blob/v0.1.2/docs/THIRD_PARTY.md) for dependency notices.
