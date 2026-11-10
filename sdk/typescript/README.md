# LeashByte TypeScript SDK

A TypeScript client for the stable LeashByte REST API. It uses types generated from the OpenAPI schema and Node's built-in `fetch` API, and it has no runtime npm dependencies.

## Install

You'll need Node 20 or later. The package is ESM only.

```bash
npm install leashbyte
```

## Use

```typescript
import { LeashByteClient } from "leashbyte";

const client = new LeashByteClient({
  baseUrl: "http://127.0.0.1:8080",
});

const page = await client.fetchPage({ url: "https://example.com/" });
const results = await client.search("self-hosted web automation", { limit: 3 });
const run = await client.createRun({
  goal: "Read the page title. Return it as the result.",
  start_url: "https://example.com/",
});
const finished = await client.waitForRun(run.id);

console.log(page.status, results.results.length, finished.state);
```

The client reads `LEASHBYTE_API_KEY` or `LEASHBYTE_API_KEY_FILE`. You can pass `apiKey` to the constructor instead, but keep the key out of your source code.

The configured key is redacted from inspection, JSON serialization and API errors. Binary methods return a `Uint8Array`. Screenshots and run artifacts can contain page data, so keep them in private storage. For browser logins, use credential placeholders. Passwords don't belong in goals, URLs or ordinary text actions.

## Build from a checkout

To work on the SDK itself, clone https://github.com/mangybyte/leashbyte and run these in `sdk/typescript`:

```bash
npm ci
npm run build
npm test
npm run licenses
```

The build writes the package to `dist/src`. To build exactly this version, check out the `v0.1.1` tag. It has the same SDK code as this package.

`npm run licenses` checks the development dependencies and writes `THIRD_PARTY_LICENSES.json`. The schema types are generated from `openapi.json` at the repository root, so run `npm run generate` whenever that file changes.
