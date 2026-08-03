# EVAL_ONLY OpenClaw repackage

This directory binds OpenClaw `2026.7.1-2` to its reviewed upstream npm tarball and changes only the
two vulnerable records in the upstream shrinkwrap:

- `brace-expansion` `5.0.7` to `5.0.8`, satisfying `minimatch`'s `^5.0.5` range;
- `fast-uri` `3.1.2` to `3.1.4`, satisfying `ajv`'s `^3.0.1` range.

`manifest-v1.json` pins every source and output by registry integrity, SHA-256, and byte size. The
build command performs two independent builds and requires byte-identical output. The independent
verifier downloads the exact pinned materials again, validates package metadata, proves that only the
approved shrinkwrap fields changed, and checks the installed plugin lock binding.

The Linux image is synthetic-evaluation infrastructure only. Its final stage pins the fixed Alpine
OpenSSL libraries and removes the base image's unused global npm tree; OpenClaw runs directly through
Node and the locked plugin tree. Its hosted workflow must build the exact image with BuildKit SLSA
provenance, create a digest-bound CycloneDX SBOM, and fail on any critical or high finding. No local
or hosted engineering evidence authorizes customer data, provider calls, public ingress, automatic
sends, direct sends, or any capability.

Rollback restores the preceding disabled upstream `openclaw@2026.7.1-2` package and placeholder image
pin. It changes no database, customer, credential, provider, DNS, channel, or deployed state.
