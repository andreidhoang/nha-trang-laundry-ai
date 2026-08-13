# TASK-provider-access-001 — provider organization, credential, and data-control posture

**Goal:** close `DEC-006` so the model can be invoked at all.

**Domains:** `runtime_architecture`, `evaluation_release`

**Stable work item:** `PROVIDER-ACCESS-001`

**Stage:** M4A
**Risk:** HIGH — this is the decision that lets customer language reach a third party.

## Why this exists

The model has never been invoked. Every one of the 33 recorded eval results carries `status: SKIP`
and `runtime_path: DETERMINISTIC_DEGRADED`, and the eval manifest declares
`PRIMARY_PROVIDER_RESULTS_MISSING` as a release blocker. `PROVIDER-TRANSPORT-001` cannot build a
real transport without a credential, `MODEL-PIN-001` cannot pin a release without a provider, and
`AGENT-002` — the G1 evidence carrier — waits on both.

`DEC-006` is owned by the Security/Privacy owner and is `OPEN`. Its fail-closed behaviour is
`NOT_SUPPORTED`, which is why the entire agent path is inert rather than partially working.

## What must be decided and recorded

`DEC-006` is not one question. It is seven, and a decision record that answers six of them does not
unblock anything:

1. **Training.** Is provider training on submitted content disabled, contractually and in the
   effective request?
2. **Retention.** What is the provider's retention period for request and response content, and
   does it match what §15 of the security specification will permit?
3. **Region.** Where is the content processed, and is that compatible with the owner's obligation?
4. **Deletion.** Can content be deleted on request, and within what period?
5. **Subprocessors.** Who else touches the content, and is the list contractually bounded?
6. **Incident terms.** What notification does the provider owe on a breach, and in what time?
7. **Credential.** A dedicated, non-personal service credential scoped to this system alone.

## Constraints

- The credential must be **dedicated and non-personal**. A personal API key belonging to a developer
  is not acceptable and cannot be rotated or revoked as a service credential.
- Do not put the credential in the repository, in an image layer, in a log, in a span attribute, or
  in any evidence file. `DEPLOY-TARGET-001` proves the absence.
- No real customer data may be sent under this item. It establishes access and posture; the first
  provider calls belong to `PROVIDER-TRANSPORT-001` and are synthetic.
- A moving model alias is `EVAL_ONLY`. Pinning the immutable release is `MODEL-PIN-001`.
- Until the decision record exists, every agent path stays `NOT_SUPPORTED`. Do not enable a flag to
  "test whether it works".

## Done when

- a dedicated non-personal credential exists and its owner, scope and rotation path are recorded;
- all seven questions above have a written answer with its source;
- the `DEC-006` decision record moves from `OPEN` to `RESOLVED` in `context/DECISION_REGISTRY.yaml`,
  by the Security/Privacy owner, with the posture attached;
- `PROVIDER-TRANSPORT-001` can start;
- no capability is authorized by this item — resolving `DEC-006` removes a blocker, it does not open
  a gate.
