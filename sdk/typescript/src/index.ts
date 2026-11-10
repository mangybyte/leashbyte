import { readFileSync } from "node:fs";
import { inspect } from "node:util";
import type { components } from "./schema.js";

export type BrowserActionRequest = components["schemas"]["BrowserActionRequest"];
export type BrowserCloseResponse = components["schemas"]["BrowserCloseResponse"];
export type BrowserSessionRequest = components["schemas"]["BrowserSessionRequest"];
export type BrowserSessionResponse = components["schemas"]["BrowserSessionResponse"];
export type BrowserSnapshotResponse = components["schemas"]["BrowserSnapshotResponse"];
export type CancelRunRequest = components["schemas"]["CancelRunRequest"];
export type CdpGrantResponse = components["schemas"]["CdpGrantResponse"];
export type ConfigResponse = components["schemas"]["ConfigResponse"];
export type CredentialListResponse = components["schemas"]["CredentialListResponse"];
export type CredentialMetadata = components["schemas"]["CredentialMetadata"];
export type CredentialRequest = components["schemas"]["CredentialRequest"];
export type FetchRequest = components["schemas"]["FetchRequest"];
export type FetchResponse = components["schemas"]["FetchResponse"];
export type RunCreateRequest = components["schemas"]["RunCreateRequest"];
export type RunListResponse = components["schemas"]["RunListResponse"];
export type RunResponse = components["schemas"]["RunResponse"];
export type SearchResponse = components["schemas"]["SearchResponse"];
export type StepResponse = components["schemas"]["StepResponse"];

export const TERMINAL_RUN_STATES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "limit_reached",
]);

export class LeashByteError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LeashByteError";
  }
}

export class ApiError extends LeashByteError {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(status: number, code: string, message: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    if (requestId !== undefined) this.requestId = requestId;
  }
}

export interface LeashByteOptions {
  baseUrl?: string;
  apiKey?: string;
  timeoutMs?: number;
  fetchImplementation?: typeof fetch;
}

interface RequestOptions {
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  headers?: Record<string, string>;
  binary?: boolean;
  extraSecrets?: string[];
}

const segment = (value: string): string => {
  if (["", ".", ".."].includes(value)) {
    throw new Error("An identifier cannot be empty or a relative path segment.");
  }
  return encodeURIComponent(value);
};

export class LeashByteClient {
  readonly baseUrl: string;
  readonly #apiKey: string;
  readonly #fetch: typeof fetch;
  readonly #timeoutMs: number;

  constructor(options: LeashByteOptions = {}) {
    const baseUrl = options.baseUrl ?? "http://127.0.0.1:8080";
    const parsed = new URL(baseUrl);
    if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
      throw new Error("The base URL must use HTTP or HTTPS and include a host.");
    }
    if (parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error("The base URL cannot contain credentials, a query or a fragment.");
    }
    const keyFile = process.env.LEASHBYTE_API_KEY_FILE;
    const fileKey = options.apiKey === undefined && keyFile ? readFileSync(keyFile, "utf8").trim() : undefined;
    const apiKey = options.apiKey || fileKey || process.env.LEASHBYTE_API_KEY;
    if (!apiKey) {
      throw new Error("Set apiKey, LEASHBYTE_API_KEY or LEASHBYTE_API_KEY_FILE.");
    }
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.#apiKey = apiKey;
    this.#fetch = options.fetchImplementation ?? fetch;
    this.#timeoutMs = options.timeoutMs ?? 30_000;
  }

  toJSON(): { baseUrl: string; apiKey: string } {
    return { baseUrl: this.baseUrl, apiKey: "[redacted]" };
  }

  [inspect.custom](): string {
    return `LeashByteClient { baseUrl: '${this.baseUrl}', apiKey: '[redacted]' }`;
  }

  #safe(value: string, extraSecrets: string[] = []): string {
    let result = value.replace(/(bearer\s+)[^\s,;]+/gi, "$1[redacted]");
    for (const secret of [this.#apiKey, ...extraSecrets]) {
      if (secret) {
        result = result.split(secret).join("[redacted]");
        result = result.split(encodeURIComponent(secret)).join("[redacted]");
      }
    }
    return result.slice(0, 2000);
  }

  async #request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const url = new URL(path.replace(/^\/+/, ""), `${this.baseUrl}/`);
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
    let response: Response;
    try {
      response = await this.#fetch(url, {
        method,
        headers: {
          "X-API-Key": this.#apiKey,
          ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
          ...options.headers,
        },
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
        signal: AbortSignal.timeout(this.#timeoutMs),
        redirect: "error",
      });
    } catch {
      throw new LeashByteError("The API request failed.");
    }
    if (!response.ok) {
      let code = "http_error";
      let message = `The API returned HTTP ${response.status}.`;
      try {
        const payload = (await response.json()) as {
          error?: { code?: string; message?: string };
        };
        code = String(payload.error?.code ?? code);
        message = String(payload.error?.message ?? message);
      } catch {
        // Keep the bounded HTTP error when the body is not the common error shape.
      }
      throw new ApiError(
        response.status,
        this.#safe(code, options.extraSecrets),
        this.#safe(message, options.extraSecrets),
        this.#safe(response.headers.get("X-Request-ID") ?? "", options.extraSecrets) || undefined,
      );
    }
    if (options.binary) {
      return new Uint8Array(await response.arrayBuffer()) as T;
    }
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      throw new LeashByteError("The API returned invalid JSON.");
    }
  }

  health(): Promise<Record<string, string>> {
    return this.#request("GET", "/healthz");
  }

  ready(): Promise<Record<string, string>> {
    return this.#request("GET", "/readyz");
  }

  getConfig(): Promise<ConfigResponse> {
    return this.#request("GET", "/v1/config");
  }

  search(
    query: string,
    options: { limit?: number; language?: string; safeSearch?: number } = {},
  ): Promise<SearchResponse> {
    return this.#request("GET", "/v1/search", {
      query: {
        q: query,
        limit: options.limit ?? 10,
        language: options.language ?? "en",
        safe_search: options.safeSearch ?? 1,
      },
    });
  }

  fetchPage(request: FetchRequest): Promise<FetchResponse> {
    return this.#request("POST", "/v1/fetch", { body: request });
  }

  createBrowserSession(request: BrowserSessionRequest): Promise<BrowserSessionResponse> {
    return this.#request("POST", "/v1/browser/sessions", { body: request });
  }

  async getBrowserSession(sessionId: string): Promise<BrowserSessionResponse> {
    return this.#request("GET", `/v1/browser/sessions/${segment(sessionId)}`);
  }

  async closeBrowserSession(sessionId: string): Promise<BrowserCloseResponse> {
    return this.#request("DELETE", `/v1/browser/sessions/${segment(sessionId)}`);
  }

  async browserSnapshot(sessionId: string): Promise<BrowserSnapshotResponse> {
    return this.#request("GET", `/v1/browser/sessions/${segment(sessionId)}/snapshot`);
  }

  async browserAction(
    sessionId: string,
    request: BrowserActionRequest,
  ): Promise<BrowserSnapshotResponse> {
    return this.#request("POST", `/v1/browser/sessions/${segment(sessionId)}/actions`, {
      body: request,
    });
  }

  async browserScreenshot(sessionId: string): Promise<Uint8Array> {
    return this.#request("GET", `/v1/browser/sessions/${segment(sessionId)}/screenshot`, {
      binary: true,
    });
  }

  async cdpGrant(sessionId: string, cdpKey: string): Promise<CdpGrantResponse> {
    return this.#request("GET", `/v1/browser/sessions/${segment(sessionId)}/cdp`, {
      headers: { "X-CDP-Key": cdpKey },
      extraSecrets: [cdpKey],
    });
  }

  createCredential(request: CredentialRequest): Promise<CredentialMetadata> {
    return this.#request("POST", "/v1/vault/credentials", { body: request });
  }

  listCredentials(): Promise<CredentialListResponse> {
    return this.#request("GET", "/v1/vault/credentials");
  }

  async replaceCredential(request: CredentialRequest): Promise<CredentialMetadata> {
    return this.#request("PUT", `/v1/vault/credentials/${segment(request.alias)}`, {
      body: request,
    });
  }

  async deleteCredential(alias: string): Promise<void> {
    await this.#request("DELETE", `/v1/vault/credentials/${segment(alias)}`);
  }

  createRun(request: RunCreateRequest): Promise<RunResponse> {
    return this.#request("POST", "/v1/runs", { body: request });
  }

  listRuns(options: { limit?: number; cursor?: string } = {}): Promise<RunListResponse> {
    return this.#request("GET", "/v1/runs", {
      query: { limit: options.limit ?? 50, cursor: options.cursor },
    });
  }

  async getRun(runId: string): Promise<RunResponse> {
    return this.#request("GET", `/v1/runs/${segment(runId)}`);
  }

  async getRunSteps(runId: string): Promise<StepResponse[]> {
    return this.#request("GET", `/v1/runs/${segment(runId)}/steps`);
  }

  async runArtifact(runId: string, name: string): Promise<Uint8Array> {
    return this.#request("GET", `/v1/runs/${segment(runId)}/artifacts/${segment(name)}`, {
      binary: true,
    });
  }

  async cancelRun(runId: string, reason = "Cancelled by the user."): Promise<RunResponse> {
    return this.#request("POST", `/v1/runs/${segment(runId)}/cancel`, {
      body: { reason } satisfies CancelRunRequest,
    });
  }

  async deleteRun(runId: string): Promise<void> {
    await this.#request("DELETE", `/v1/runs/${segment(runId)}`);
  }

  async waitForRun(
    runId: string,
    options: { timeoutMs?: number; intervalMs?: number; signal?: AbortSignal } = {},
  ): Promise<RunResponse> {
    const timeoutMs = options.timeoutMs ?? 600_000;
    const intervalMs = options.intervalMs ?? 1_000;
    if (timeoutMs <= 0 || intervalMs <= 0) {
      throw new Error("The polling timeout and interval must be greater than zero.");
    }
    const deadline = Date.now() + timeoutMs;
    while (true) {
      options.signal?.throwIfAborted();
      const run = await this.getRun(runId);
      if (TERMINAL_RUN_STATES.has(run.state)) return run;
      if (Date.now() >= deadline) {
        throw new LeashByteError("The run did not finish before the polling timeout.");
      }
      await new Promise<void>((resolve, reject) => {
        const onAbort = () => {
          clearTimeout(timer);
          reject(new LeashByteError("Run polling was cancelled."));
        };
        const timer = setTimeout(() => {
          options.signal?.removeEventListener("abort", onAbort);
          resolve();
        }, Math.min(intervalMs, Math.max(0, deadline - Date.now())));
        options.signal?.addEventListener("abort", onAbort, { once: true });
      });
    }
  }
}
