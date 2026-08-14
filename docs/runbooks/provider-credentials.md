# Runbook — where a model-provider credential lives

**Status:** normative for credential *handling*. It authorizes no model call.
**Audience:** every human and every agent working in this repository.
**Anchors:** `docs/adr/0003-provider-neutral-agent-runtime-and-channel-operations.md`,
`docs/adr/0007-production-deployment-topology.md` §rotation,
`specs/SECURITY_RELIABILITY_SPEC_V1.md`, `DEC-006`, `DEC-009`.

This runbook exists because the question "where is the API key stored so the model can be called?"
has a precise answer in this repository and the answer has two halves. People reliably read the first
half and skip the second.

1. **Where it lives:** the process environment, or `/run/secrets` in a container. Never a file in
   this repository.
2. **What that permits:** nothing yet. A credential being present is not authorization to call a
   provider. The gate is `DEC-006`, the `provider_data_gate` block in `runtime/model-registry-v1.yaml`,
   and a signed release gate — not the existence of a key.

**Tiếng Việt, ngắn:** key nằm trong biến môi trường (hoặc `/run/secrets`), **không bao giờ** nằm trong
repo. Và **có key không có nghĩa là được phép gọi model** — cổng chặn là `DEC-006` + `provider_data_gate`
+ release gate đã ký, không phải sự tồn tại của key.

---

## 1. The storage rule

| | |
|---|---|
| **Canonical location** | the process environment of the service that calls the provider |
| **Container location** | a file under `/run/secrets`, one secret per file — `WorkerSettings` reads that directory when it exists (`apps/worker/src/nha_trang_laundry_worker/host.py`) |
| **Reserved variable name** | `AGENT_PROVIDER_API_KEY` — one variable for whichever provider the pinned registry selects |
| **Never** | committed to the repository, written into `runtime/model-registry-v1.yaml`, written into any file under `evidence/`, pasted into a chat transcript, echoed into a log line, or passed as a command-line argument |

`WorkerSettings` sets `env_file=None` deliberately. The worker does not load a `.env` file; a
developer `.env` is for local tooling only and is git-ignored. Do not add `env_file` to make a
credential load more conveniently.

**One variable, not one per vendor.** The provider is chosen by `model.provider` in
`runtime/model-registry-v1.yaml`, which is hash-pinned. If the runtime instead inferred its provider
from which vendor-shaped variable happened to be set, the environment would silently outrank the
pinned registry and `MODEL-PIN-001` would be unenforceable — a deployment could change providers with
no change to any pinned file. The registry names the provider; the environment supplies only the
secret for it.

## 2. What actually reads a provider credential today: nothing

This is the part that surprises people, so it is stated with the evidence.

Repository-wide there is exactly one provider-key reference in any executable path:

```
scripts/verify_agent_runtime.py:122   "OPENAI_API_KEY": "validation-only-not-a-real-provider-key"
```

That is a deliberate placeholder passed to `openclaw config validate` and `openclaw security audit`,
both of which are offline checks. It is not a call. There is no other credential read: no
`AGENT_PROVIDER_API_KEY` field on `WorkerSettings`, no provider client, no HTTP call to any inference
endpoint.

Wiring the read is `PROVIDER-TRANSPORT-001`. Until that item lands, setting the variable has no
effect on any code path, and an agent that reports "the model is now configured" because a variable
is exported has reported something untrue.

`AGENT_PROVIDER_API_KEY` is therefore a **reserved** name, not yet an implemented one. The embedded
OpenClaw cell documents its own `OPENAI_API_KEY` convention, which is why the offline validator passes
that name. Reconciling the two — one variable read by the transport, whatever the vendor SDK beneath it
expects — is `PROVIDER-TRANSPORT-001`'s call to confirm. Until it does, this runbook governs *handling*
and the transport packet governs *naming*.

## 3. Possession is not authorization

Before any real provider call — even with synthetic data — all of the following must hold. Each is a
file you can read, not a judgement call:

| Gate | Where | Current value |
|---|---|---|
| `DEC-006` resolved | `context/DECISION_REGISTRY.yaml` | `OPEN`, owner `SECURITY_PRIVACY_OWNER` |
| Security and Privacy approval recorded | `provider_data_gate` in `runtime/model-registry-v1.yaml` | `NOT_APPROVED` / `NOT_APPROVED` |
| Dedicated service credential verified | same block | `NOT_VERIFIED` |
| Provider data-controls evidence for that provider | `evidence/provider/` | exists for OpenAI only |
| Real customer model calls enabled | `activation` in the registry | `false` |
| Transport implemented | `PROVIDER-TRANSPORT-001` | not delivered |

For customer-supplied **media** specifically there is a second, independent gate: `DEC-009` is `OPEN`
with fail-closed `NOT_SUPPORTED`. No image, attachment or document byte may be fetched and sent to any
inference endpoint until that decision is answered, regardless of which credentials exist.

**A credential for a provider with no `evidence/provider/` record authorizes nothing at all.** As of
this document that includes every provider except OpenAI, and `specs/contracts/provider-data-evidence-v1.schema.json`
pins `provider` to `const: "openai"` — so a record for another vendor cannot even be written without a
schema version, and that schema is itself one of the 21 hash-pinned files. See
`docs/TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md` §3.6.

## 4. Rules for an agent working in this repository

- **Never write a credential value anywhere.** Not into a file, not into a commit, not into a task
  packet, not into an evidence record, not into a memory file, not back into the conversation.
- **Never make a provider call.** Provider calls, secrets and capability authorization are outside
  what an agent may decide (`CLAUDE.md` §"What an agent may not decide here"). This holds even for a
  one-off "just checking the key works" request.
- **A pasted credential is a leaked credential.** If one appears in a transcript, say so and tell the
  owner to rotate it. Do not carry it forward.
- **Refer to a credential by variable name, never by value** — `AGENT_PROVIDER_API_KEY`, not the
  characters it holds.
- **Do not relax a gate to make a flow complete.** An unset credential means `NOT_SUPPORTED`, which is
  the correct terminal outcome, not a bug to work around.

## 5. Rotation

`docs/adr/0007-production-deployment-topology.md` requires that rotating the provider API key be
exercised before G1, alongside the database credential. Rotation is an owner action. An agent may
prepare the runbook step and must not perform it.

Rotate immediately, without waiting for a scheduled window, when a key has been pasted into a chat,
a ticket, a log, a screenshot, or any shared document.

## 6. Known gap in automatic scrubbing

The log redactor (`packages/observability/src/nha_trang_laundry_observability/redaction.py`) and the
evidence-record guards (`scripts/record_delivery_evidence.py`, `scripts/manage_automation_state.py`)
match credentials by shape. They recognise `Bearer`/`Basic` headers, `sk-` prefixed keys,
`name = value` assignment forms, JWT triples, and — since this document — NVIDIA `nvapi-`/`nvcf-`
prefixes.

A committed secret is separately scanned by gitleaks, but only inside
`.github/workflows/release-supply-chain.yml` — there is no pre-commit hook. That scan therefore
reports a leak after the commit exists, and whether its default ruleset covers a given vendor prefix
is not verified here.

They do **not** recognise arbitrary future vendor prefixes. Automatic scrubbing is a safety net for
mistakes, never the primary control. The primary control is rule 1 of §4: the value is never written
down. When a new provider is introduced, extend all three pattern sets in the same change; the
duplication between them is deliberate, because `scripts/manage_automation_state.py` is stdlib-only by
design and must keep working with no workspace packages importable.
