import assert from "node:assert/strict";
import test from "node:test";

import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";

import entry from "./index.js";
import { OPERATIONS } from "./operation-contracts.js";

const SERVER_FIELDS = new Set([
  "actor_id", "actor_role", "address_id", "capability", "contact_id", "conversation_id",
  "customer_id", "distance_measurement_id", "order_id", "order_request_id", "policy_version",
  "reason_codes", "required_approver_role", "stage", "store_id", "tenant_id", "ttl",
]);

function schemaKeys(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(schemaKeys);
  if (value === null || typeof value !== "object") return [];
  return Object.entries(value).flatMap(([key, child]) => [key, ...schemaKeys(child)]);
}

test("plugin exposes exactly the generated fixed-name tool inventory", () => {
  const metadata = getToolPluginMetadata(entry);
  assert.ok(metadata);
  assert.deepEqual(
    metadata.tools.map((tool) => tool.name).sort(),
    Object.values(OPERATIONS).map((operation) => operation.toolName).sort(),
  );
  assert.equal(metadata.tools.length, 10);
});

test("model schemas exclude server-owned binding and authority fields", () => {
  for (const definition of Object.values(OPERATIONS)) {
    const exposed = new Set(schemaKeys(definition.parameters));
    for (const field of SERVER_FIELDS) assert.equal(exposed.has(field), false, `${definition.operationId}: ${field}`);
  }
});

test("plugin has no direct-send or generic execution tool", () => {
  const toolNames = Object.values(OPERATIONS).map((operation) => operation.toolName);
  assert.equal(toolNames.some((name) => /send|exec|shell|browser|fetch|sql|generic/.test(name)), false);
});
