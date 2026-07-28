# Release gate manifests

This directory contains only signed capability release manifests that validate against
`specs/contracts/release-gate-manifest-v1.schema.json`.

No manifest means **not authorized**. A valid schema alone is insufficient: it must be unexpired,
signed by the required independent roles, reference retrievable hash-verified evidence, and match the
capability/stage being enabled.

Do not create placeholder manifests for unimplemented capabilities.

