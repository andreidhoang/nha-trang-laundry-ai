# Public OpenClaw cell

This directory is an **eval-only build artifact**, not a public-channel authorization.

- Production target: a dedicated Linux VM/VPS and OS identity.
- Gateway/control UI: loopback only; the control UI and terminal are disabled.
- Runtime: one embedded `public-concierge`, no fallback model or sub-agents.
- Tools: only the ten fixed tools generated from `agent-tools-v1.openapi.yaml`.
- Secrets: process-injected dedicated credentials; never owner/personal credentials.
- Plugin path: `AGENT_TOOL_PLUGIN_PATH` points to the built, read-only pinned plugin package.
- Network: host egress policy allows only the approved model endpoint and Agent Tool Facade. The
  sandbox itself has no network.
- State: ephemeral/non-authoritative; PostgreSQL remains durable truth.

The checked-in configuration intentionally contains an unresolved sandbox image digest and an eval
model alias. Deployment and real-customer model processing remain disabled until a scanned image,
immutable model release, provider effective-request verification, Security/Privacy approvals, full
integrated evals and a signed release manifest are recorded.

OpenClaw 2026.7.1-2 documents that direct OpenAI Responses routes force `store: true` by default.
`responsesServerCompaction: false` does not change that behavior. The product requires `store: false`;
until a supported override and the captured effective request prove it, this cell is synthetic eval only.
