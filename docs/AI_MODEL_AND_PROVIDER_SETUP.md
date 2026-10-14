# AI model and provider setup — what an engineer or Codex does, and may not do

**Status:** engineering guidance and a *recommendation*. It decides nothing and authorizes no model
call. `DEC-006` (provider data use, retention, region, deletion) is **OPEN**, owner
`SECURITY_PRIVACY_OWNER`. All 13 capabilities are `NOT_AUTHORIZED`: `delivery/CAPABILITY_STATUS.yaml`
has rows for only 3 (`INTERNAL_SHADOW`, `PUBLIC_FAQ`, `LIST_PRICE_INFO`) and sets
`default_authorization: NOT_AUTHORIZED` for the rest, with `delivery/GATE_REGISTRY.yaml`
`capability_requirements` listing all 13. Credential handling: `docs/runbooks/provider-credentials.md`.
Which model runs where, and what is built vs not wired: **`docs/AI_ROLE_MAP.md`**.

**Tiếng Việt, ngắn:** hiện chưa có đoạn code nào đọc API key; chưa được gọi model thật cho tới khi
`DEC-006` được chủ sở hữu ký. Bản này chỉ nói *nên chọn model nào* và *kỹ sư làm gì, theo thứ tự nào*.

## 1. Truth today (re-verify before relying on it)

| Fact | Where to check |
|---|---|
| Pinned model: provider `openai`, `api_model_id: gpt-5.6-terra`, transport `responses`, `EVAL_ONLY`, `real_customer_model_calls_enabled: false` | `runtime/model-registry-v2.yaml` |
| Worker runtime speaks the OpenAI **Responses** protocol, bounded: ≤3 model calls, ≤6 tool calls, 20 s, 8,000 input / 1,200 output tokens, $0.10 per turn | `apps/worker/src/nha_trang_laundry_worker/responses_runtime.py`, registry `limits` |
| **No code reads a provider key and no HTTP call to any inference endpoint exists** | `docs/runbooks/provider-credentials.md` §2 |
| The key name is reserved: `AGENT_PROVIDER_API_KEY` (env or `/run/secrets`), never a repo file | same runbook §1 |
| Model never computes money, decides policy/SLA/state/permission, selects a customer, or sends | `CLAUDE.md`, `AGENTS.md` |

## 2. Which model — recommendation (not a decision; owner and DEC-006 decide)

Ranked for this app's use: Vietnamese customer messages, tool-calling against the 10-operation Tool
Facade, draft-or-`REQUIRE_HUMAN` output, low cost per conversation, data-region control.

1. **Gemini Flash family via Vertex AI, Singapore region** — provisional #1: cheapest per
   conversation (see `docs/UNIFIED_CUSTOMER_AGENT_DECISION_2026-09.md` cost table), strong
   Vietnamese, region choice supports DEC-006. Not Responses-native, so it needs an adapter (§4).
2. **Claude Sonnet 5.5** — best instruction-following and tool discipline; higher price. Fallback
   if Gemini's Vietnamese or tool accuracy loses the bake-off.
3. **GPT-5 mini** — works with the existing Responses transport with no adapter; the safe baseline
   and the only provider with data-controls evidence in `evidence/provider/` today.

Caveat: this ranking came from search snippets; several vendor pages were not fetchable from the
research environment. Treat it as a hypothesis the bake-off (§4 step 3) must confirm with our own
data. Do not cite it as evidence.

## 3. What may be done now, with no credential and no decision

- Read the runtime, registry and tool contract; write failing tests for the provider adapter
  interface against **recorded/synthetic fixtures**.
- Keep every flag false. Run the synthetic evals (`packages/evals`) — they are `provider_backed: false`.

## 4. Order of work once the owner signs DEC-006 (each step is a delivery-queue item)

1. `PROVIDER-TRANSPORT-001` — implement the provider client behind the existing runtime interface;
   read `AGENT_PROVIDER_API_KEY` in `WorkerSettings` (env/`/run/secrets`, `env_file=None` stays).
   Add a thin adapter so a non-Responses provider (Gemini/Claude) maps to the same bounded FSM.
2. `MODEL-PIN-001` — pin the chosen `api_model_id` and an immutable release id in
   `runtime/model-registry-v2.yaml`; update `provider_data_gate` only from evidence, never by hand.
3. Bake-off (eval engineer): the same Vietnamese synthetic corpus through all three candidates;
   score tool-call accuracy, REQUIRE_HUMAN correctness, Vietnamese quality, latency, cost per turn.
   The winner is chosen from this data, then the owner approves.
4. Authorize capabilities one at a time, `INTERNAL_SHADOW` first, each with a signed manifest.

## 5. Hard rules for Codex

- Never put a key in the repo, evidence, logs, chat or command line. Never invent or ask for one.
- Never flip `real_customer_model_calls_enabled`, `provider_data_gate`, or any capability status.
- A key being present is not permission. A green test is not evidence of authorization.
- Unknown or ambiguous → record a blocker through the controller and stop (`AGENTS.md`).
