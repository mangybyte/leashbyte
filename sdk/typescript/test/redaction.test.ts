import { inspect } from "node:util";
import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, LeashByteClient } from "../src/index.js";

test("the planted API key never appears in logs, inspect output or errors", async () => {
  const marker = "planted-typescript-key-91c47e";
  const mockFetch: typeof fetch = async (_input, init) => {
    const headers = init?.headers as Record<string, string>;
    assert.equal(headers["X-API-Key"], marker);
    return new Response(
      JSON.stringify({ error: { code: `denied-${marker}`, message: `Bad key: ${marker}` } }),
      { status: 401, headers: { "Content-Type": "application/json", "X-Request-ID": marker } },
    );
  };
  const client = new LeashByteClient({ apiKey: marker, fetchImplementation: mockFetch });
  let logged = "";
  const originalLog = console.log;
  console.log = (...args: unknown[]) => {
    logged += args.map((value) => inspect(value)).join(" ");
  };
  try {
    console.log(client);
  } finally {
    console.log = originalLog;
  }
  assert.doesNotMatch(logged, new RegExp(marker));
  assert.doesNotMatch(inspect(client), new RegExp(marker));
  assert.doesNotMatch(JSON.stringify(client), new RegExp(marker));
  await assert.rejects(client.getConfig(), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.doesNotMatch(error.message, new RegExp(marker));
    assert.doesNotMatch(error.code, new RegExp(marker));
    assert.doesNotMatch(String(error.requestId), new RegExp(marker));
    return true;
  });
});

test("the client keeps base URL paths and rejects redirects and relative IDs", async () => {
  let requested = "";
  let redirect = "";
  const mockFetch: typeof fetch = async (input, init) => {
    requested = String(input);
    redirect = init?.redirect ?? "";
    return new Response(JSON.stringify({ status: "ok" }), {
      headers: { "Content-Type": "application/json" },
    });
  };
  const previousKeyFile = process.env.LEASHBYTE_API_KEY_FILE;
  process.env.LEASHBYTE_API_KEY_FILE = "/missing/leashbyte-test-key";
  let client: LeashByteClient;
  try {
    client = new LeashByteClient({
      baseUrl: "https://example.invalid/leashbyte/",
      apiKey: "test-key",
      fetchImplementation: mockFetch,
    });
  } finally {
    if (previousKeyFile === undefined) delete process.env.LEASHBYTE_API_KEY_FILE;
    else process.env.LEASHBYTE_API_KEY_FILE = previousKeyFile;
  }
  await client.health();
  assert.equal(requested, "https://example.invalid/leashbyte/healthz");
  assert.equal(redirect, "error");
  await assert.rejects(client.getRun(".."), /relative path/);
  const invalidErrorClient = new LeashByteClient({
    apiKey: "test-key",
    fetchImplementation: async () =>
      new Response(JSON.stringify({ error: { code: 401 } }), { status: 401 }),
  });
  await assert.rejects(invalidErrorClient.health(), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.code, "401");
    return true;
  });
});
