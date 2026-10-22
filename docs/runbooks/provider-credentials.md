# Runbook — where a model-provider credential lives

Which model to evaluate and in what order: `docs/AI_MODEL_AND_PROVIDER_SETUP.md`.

**Status:** normative for credential *handling*. It authorizes no model call.
**Audience:** every human and every agent working in this repository.
**Anchors:** `docs/adr/0003-provider-neutral-agent-runtime-and-channel-operations.md`,
`docs/adr/0007-production-deployment-topology.md` §rotation,
`specs/SECURITY_RELIABILITY_SPEC_V1.md`, `DEC-006`, `DEC-009`.

**Scoped local authorization, October 15, 2026:** the user separately permits implementation and
synthetic local Gemini verification. `local-gemini-evaluation.md` defines its default-off, demo-only
guard and persistent attempt cap. A live run also needs an explicit spending allowance. This
exception does not authorize customer data, public capabilities or production deployment. It is
recorded in `docs/audit/LOCAL_READINESS_2026-10-15.md`.

This runbook exists because the question "where is the API key stored so the model can be called?"
has a precise answer in this repository and the answer has two halves. People reliably read the first
half and skip the second.

1. **Where it lives:** the process environment, or `/run/secrets` in a container. Never a file in
   this repository.
2. **What that permits:** nothing yet. A credential being present is not authorization to call a
   provider. The gate is `DEC-006`, the `provider_data_gate` block in `runtime/model-registry-v2.yaml`,
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
| **Variable name** | `AGENT_PROVIDER_API_KEY` — implemented by the local classifier; reserved for the separately pinned public runtime |
| **Never** | committed to the repository, written into `runtime/model-registry-v2.yaml`, written into any file under `evidence/`, pasted into a chat transcript, echoed into a log line, or passed as a command-line argument |

`WorkerSettings` sets `env_file=None` deliberately. The worker does not load a `.env` file; a
developer `.env` is for local tooling only and is git-ignored. Do not add `env_file` to make a
credential load more conveniently.

**One variable, not one per vendor.** The provider is chosen by `model.provider` in
`runtime/model-registry-v2.yaml`, which is hash-pinned. If the runtime instead inferred its provider
from which vendor-shaped variable happened to be set, the environment would silently outrank the
pinned registry and `MODEL-PIN-001` would be unenforceable — a deployment could change providers with
no change to any pinned file. The registry names the provider; the environment supplies only the
secret for it.

## 2. Implemented credential reader

`apps/api/src/nha_trang_laundry_api/assistant_gemini.py` reads `AGENT_PROVIDER_API_KEY` as a
`SecretStr` from the process environment after the local demo authorization guards. It uses a fixed
Gemini endpoint and model; it does not choose a provider by detecting vendor-shaped variables.
The local settings do not load an environment file. Setting the key alone still enables nothing.

The public worker still has no credential field or live provider transport. That separate work is
`PROVIDER-TRANSPORT-001`; the local assistant does not complete or authorize it.

## 3. Possession is not authorization

For the public runtime and all real customer processing, all of the following must hold. The
separately authorized local synthetic evaluation above has its own narrower guard and evidence:

| Gate | Where | Current value |
|---|---|---|
| `DEC-006` resolved | `context/DECISION_REGISTRY.yaml` | `OPEN`, owner `SECURITY_PRIVACY_OWNER` |
| Security and Privacy approval recorded | `provider_data_gate` in `runtime/model-registry-v2.yaml` | `NOT_APPROVED` / `NOT_APPROVED` |
| Dedicated service credential verified | same block | `NOT_VERIFIED` |
| Provider data-controls evidence for that provider | `evidence/provider/` | exists for OpenAI only |
| Real customer model calls enabled | `activation` in the registry | `false` |
| Transport implemented | `PROVIDER-TRANSPORT-001` | not delivered |

For media there is a second, independent decision: `DEC-009` is `RESOLVED` for staff-supplied media
only. Customer-supplied media remains prohibited, and the local classifier accepts text only.

**A credential alone authorizes nothing. Public release requires a provider evidence record.** As of
this document that includes every provider except OpenAI, and `specs/contracts/provider-data-evidence-v1.schema.json`
pins `provider` to `const: "openai"` — so a record for another vendor cannot even be written without a
schema version, and that schema is itself one of the 21 hash-pinned files. See
`docs/TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md` §3.6.

## 4. Rules for an agent working in this repository

- **Never write a credential value anywhere.** Not into a file, not into a commit, not into a task
  packet, not into an evidence record, not into a memory file, not back into the conversation.
- **Never infer provider-call permission from a key.** The user must explicitly authorize the
  destination, purpose and cost scope. The scoped local authorization above grants no production
  capability or account-level privacy approval.
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
`name = value` assignment forms, JWT triples, and — since this document — NVIDIA `nvapi-`/`nvcf-`, Google `AIza` and `AQ.` credential forms. All three guards
have synthetic regression cases for these prefixes.

A committed secret is separately scanned by gitleaks, but only inside
`.github/workflows/release-supply-chain.yml` — there is no pre-commit hook. That scan therefore
reports a leak after the commit exists, and whether its default ruleset covers a given vendor prefix
is not verified here.

They do **not** recognise arbitrary future vendor prefixes. Automatic scrubbing is a safety net for
mistakes, never the primary control. The primary control is rule 1 of §4: the value is never written
down. When a new provider is introduced, extend all three pattern sets in the same change; the
duplication between them is deliberate, because `scripts/manage_automation_state.py` is stdlib-only by
design and must keep working with no workspace packages importable.
