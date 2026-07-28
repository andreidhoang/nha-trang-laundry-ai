import { createHash } from "node:crypto";

import type { OperationDefinition } from "./operation-contracts.js";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PUBLIC_CODE_PATTERN = /^[A-Za-z0-9_-]{16,64}$/;

export interface RuntimeBinding {
  readonly bindingId: string;
  orderRequestId?: string;
  rowVersion: number;
  readonly publicCode?: string;
}

const bindings = new Map<string, RuntimeBinding>();

export function parseRuntimeBinding(sessionKey: string | undefined): RuntimeBinding {
  if (!sessionKey) {
    throw new Error("TOOL_UNAVAILABLE: missing server-owned agent session binding");
  }
  const existing = bindings.get(sessionKey);
  if (existing) return existing;
  const parts = sessionKey.split(":");
  if (parts.length !== 5 || parts[0] !== "laundry-public") {
    throw new Error("TOOL_UNAVAILABLE: malformed server-owned agent session binding");
  }
  const [, bindingId, requestPart, versionPart, publicCodePart] = parts;
  if (!bindingId || !UUID_PATTERN.test(bindingId)) {
    throw new Error("TOOL_UNAVAILABLE: invalid run binding identifier");
  }
  const orderRequestId = requestPart === "-" ? undefined : requestPart;
  if (orderRequestId !== undefined && !UUID_PATTERN.test(orderRequestId)) {
    throw new Error("TOOL_UNAVAILABLE: invalid bound order request");
  }
  const rowVersion = Number(versionPart);
  if (!Number.isSafeInteger(rowVersion) || rowVersion < 0) {
    throw new Error("TOOL_UNAVAILABLE: invalid bound aggregate version");
  }
  const publicCode = publicCodePart === "-" ? undefined : publicCodePart;
  if (publicCode !== undefined && !PUBLIC_CODE_PATTERN.test(publicCode)) {
    throw new Error("TOOL_UNAVAILABLE: invalid bound public order code");
  }
  const binding: RuntimeBinding = {
    bindingId,
    rowVersion,
    ...(orderRequestId === undefined ? {} : { orderRequestId }),
    ...(publicCode === undefined ? {} : { publicCode }),
  };
  bindings.set(sessionKey, binding);
  return binding;
}

function runnerBridgeBaseUrl(): URL {
  const raw = process.env.AGENT_RUNNER_BRIDGE_BASE_URL;
  if (!raw) throw new Error("TOOL_UNAVAILABLE: Agent Runner bridge is not configured");
  const url = new URL(raw);
  const local = url.hostname === "127.0.0.1" || url.hostname === "localhost" || url.hostname === "::1";
  if (url.protocol !== "http:" || !local) {
    throw new Error("TOOL_UNAVAILABLE: Agent Runner bridge must use local loopback");
  }
  url.pathname = url.pathname.replace(/\/$/, "") + "/";
  return url;
}

function runnerBridgeToken(): string {
  const token = process.env.AGENT_RUNNER_BRIDGE_TOKEN;
  if (!token || token.length < 32) {
    throw new Error("TOOL_UNAVAILABLE: Agent Runner bridge credential is not configured");
  }
  return token;
}

function boundPath(definition: OperationDefinition, binding: RuntimeBinding): string {
  let path: string = definition.path;
  if (path.includes("{order_request_id}")) {
    if (!binding.orderRequestId) {
      throw new Error("MISSING_REQUIRED_FACT: no order request is bound to this run");
    }
    path = path.replace("{order_request_id}", encodeURIComponent(binding.orderRequestId));
  }
  if (path.includes("{public_code}")) {
    if (!binding.publicCode) {
      throw new Error("MISSING_REQUIRED_FACT: no public order code is bound to this run");
    }
    path = path.replace("{public_code}", encodeURIComponent(binding.publicCode));
  }
  if (path.includes("{")) throw new Error("TOOL_UNAVAILABLE: unresolved bound path parameter");
  return path.replace(/^\//, "");
}

function idempotencyKey(bindingId: string, operationId: string, toolCallId: string): string {
  const digest = createHash("sha256")
    .update(`${bindingId}\0${operationId}\0${toolCallId}`, "utf8")
    .digest("hex");
  return `agt-${digest.slice(0, 48)}`;
}

export async function invokeFacade(
  definition: OperationDefinition,
  params: unknown,
  binding: RuntimeBinding,
  toolCallId: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const headers = new Headers({
    Accept: "application/json",
    Authorization: `Bearer ${runnerBridgeToken()}`,
    "X-Agent-Run-Binding": binding.bindingId,
  });
  const headerParameters = definition.headerParameters as readonly string[];
  if (headerParameters.includes("Idempotency-Key")) {
    headers.set("Idempotency-Key", idempotencyKey(binding.bindingId, definition.operationId, toolCallId));
  }
  if (headerParameters.includes("If-Match")) {
    if (binding.rowVersion < 1) {
      throw new Error("STALE_VERSION: bound order request has no mutable version");
    }
    headers.set("If-Match", `"${binding.rowVersion}"`);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("TOOL_UNAVAILABLE: facade timeout")), 3000);
  const abortFromCaller = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", abortFromCaller, { once: true });
  try {
    let body: string | undefined;
    if (definition.method !== "GET") {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(params);
    }
    const response = await fetch(new URL(boundPath(definition, binding), runnerBridgeBaseUrl()), {
      method: definition.method,
      headers,
      ...(body === undefined ? {} : { body }),
      signal: controller.signal,
    });
    const payload: unknown = await response.json().catch(() => ({
      ok: false,
      error: { code: "TOOL_UNAVAILABLE", message: "Facade returned non-JSON output." },
    }));
    const etag = response.headers.get("etag");
    if (etag && /^"[1-9][0-9]*"$/.test(etag)) binding.rowVersion = Number(etag.slice(1, -1));
    if (definition.operationId === "orderRequestCreate" && response.ok) {
      const candidate = (payload as { data?: { order_request_id?: unknown; version?: unknown } }).data;
      if (candidate && typeof candidate.order_request_id === "string" && UUID_PATTERN.test(candidate.order_request_id)) {
        binding.orderRequestId = candidate.order_request_id;
        if (Number.isSafeInteger(candidate.version) && Number(candidate.version) > 0) {
          binding.rowVersion = Number(candidate.version);
        }
      }
    }
    return payload;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}
