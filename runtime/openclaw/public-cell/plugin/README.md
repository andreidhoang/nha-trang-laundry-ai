# Nha Trang Laundry OpenClaw tools

Generated, fixed-name tool adapter for the normative Agent Tool Facade OpenAPI contract. Model-facing
parameters contain request bodies only. Path IDs, idempotency, concurrency headers, actor, stage and
authorization context are derived from an authenticated Agent Runner binding and revalidated by the
facade.

The plugin has no channel, send, browser, shell, filesystem, generic HTTP or configuration-mutation
tool. It calls only the loopback Agent Runner bridge using `AGENT_RUNNER_BRIDGE_BASE_URL` and an
ephemeral `AGENT_RUNNER_BRIDGE_TOKEN`. The bridge—not OpenClaw—holds the short-lived `runnerBearer`
JWT and forwards the fixed operation to the Tool Facade. Neither token is returned to the model or
logged.
