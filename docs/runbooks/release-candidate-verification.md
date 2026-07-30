# Release candidate verification

This procedure verifies supplied evidence. It does not generate signers, signatures, approvals, or
release authority, and it does not enable a capability.

## Preconditions

- Obtain the release manifest and immutable evidence package from the approved evidence system.
- Obtain the trusted signer registry separately. It contains public keys only.
- Obtain the registry's `sha256:<64 lowercase hex>` pin from deployment configuration or the approved
  trust-root system. Never copy the hash from the registry being verified.
- Identify the exact deployed 40-character Git commit, stage, capability, and verification time.
- Keep secrets, private keys, raw PII, provider payloads, and chain-of-thought outside the repository.

## Verification

Run against a read-only extracted evidence directory:

```text
uv run python scripts/verify_release_candidate.py \
  --manifest <release-manifest.json> \
  --trusted-signers <trusted-release-signers.json> \
  --trusted-signers-sha256 <out-of-band-sha256-pin> \
  --expected-commit-sha <deployed-40-character-commit> \
  --stage SHADOW \
  --capability INTERNAL_SHADOW \
  --artifact-root <read-only-evidence-root> \
  --at <rfc3339-verification-time>
```

Success prints only the verified envelope, canonical payload hash, validity window, and artifact count.
Any missing artifact, hash mismatch, schema violation, wrong deployment envelope, untrusted signer,
identity reuse, invalid signature, premature activation, or expiry exits nonzero.

## Activation boundary

The command output is diagnostic, not a bearer token and not release evidence. Production startup must
load and verify the same manifest and trust-root pin in process, then provide the resulting
`VerifiedReleaseAuthorization` to `AgentRunner`. Runtime registry changes alone cannot authorize a
provider call. Capability status remains `NOT_AUTHORIZED` until the signed manifest is recorded in the
machine capability state by the authorized release process.

An `AUTHORIZED` capability status is accepted only when deployment independently supplies
`RELEASE_DEPLOYED_COMMIT_SHA`, `RELEASE_DEPLOYMENT_STAGE`, and
`RELEASE_TRUSTED_SIGNERS_SHA256`. These values must come from deployment metadata and the approved
trust-root system, never from `delivery/CAPABILITY_STATUS.yaml` or the manifest being verified.
