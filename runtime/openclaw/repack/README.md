# EVAL_ONLY OpenClaw repackage

This directory binds OpenClaw `2026.7.1-2` to its reviewed upstream npm tarball. The current derived
revision, `r2`, starts from the immutable disabled `r1` artifact and permits exactly four reviewed
replacements:

- `brace-expansion` `5.0.8` to `5.0.9`, satisfying `minimatch`'s `^5.0.5` range;
- `fast-uri` `3.1.4` to `3.1.5`, satisfying `ajv`'s `^3.0.1` range;
- `ip-address` `10.2.0` to `10.3.1`, satisfying `express-rate-limit`'s `^10.2.0` range;
- `undici` `8.5.0` to `8.9.0`, with the exact reviewed `package/package.json` dependency mutation.

`manifest-v2.json` pins the upstream, r1 base, every source and replacement package, and r2 output by
name, path, version, registry URL, integrity, SHA-256, byte size, required-by path/range, and expected
package metadata. The builder emits a canonical tar/gzip stream with normalized ordering, UTF-8 POSIX
paths, timestamps, ownership, permissions, tar headers, and gzip metadata. It performs two independent
builds and requires byte-identical output. The independent verifier reconstructs and compares the
archive, validates the complete tree, and rejects every mutation outside `package/package.json` and
the explicitly allowlisted shrinkwrap records.

The Linux image is synthetic-evaluation infrastructure only. Its final stage pins the fixed Alpine
OpenSSL libraries and removes the base image's unused global npm tree; OpenClaw runs directly through
Node and the locked plugin tree. Its hosted workflow must build the exact image with BuildKit SLSA
provenance, create a digest-bound CycloneDX SBOM, and fail on any critical or high finding. No local
or hosted engineering evidence authorizes customer data, provider calls, public ingress, automatic
sends, direct sends, or any capability.

Rollback restores the preceding disabled derived `r1` artifact (or its pinned upstream source) and
placeholder image pin. It changes no database, customer, credential, provider, DNS, channel, or
deployed state.
