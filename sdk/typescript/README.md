# LeashByte TypeScript SDK

A TypeScript client for the stable LeashByte REST API. It uses types generated from the OpenAPI schema and Node's built-in `fetch` API, and it has no runtime npm dependencies.

The npm package isn't published yet, so build it from a GitHub checkout.

## Build

You'll need Node 20 or later.

```bash
npm ci
npm run build
```

## Use

```typescript
import { LeashByteClient } from "./dist/src/index.js";

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

## Checks

```bash
npm test
npm run licenses
```

`npm run licenses` checks the development dependencies and writes `THIRD_PARTY_LICENSES.json`. The schema types are generated from `openapi.json` at the repository root, so run `npm run generate` whenever that file changes.
