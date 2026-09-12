# Contributor guidance

This repository is the staged public boundary for the memoriesQL open core. Keep changes provider-neutral, deterministic, content-blind, and default-deny.

- `contracts/public-registry.json` is the sole authority for generated catalogs.
- Never discover export candidates by scanning another repository.
- Do not add desktop UI, installers, update channels, product module manifests, provider-specific discovery or parsing, private evidence, owner data, or credentials.
- The approved migration substrate is limited to the explicit migration inventory, runner, schema inspection and resource closure. Preserve SQL 0001–0018 byte-for-byte. The runtime cut is limited to `contracts/runtime-inventory.json`: neutral runtime and internal static mechanics, with caller-supplied composition. The public-authored schema-15 transition adds only the inventoried immutable-observation commands, task definitions and adapter dispatch. The public-authored schema-16 transition adds only version-1 complete-input evidence package storage and bounded authorized reads; see `docs/evidence-packages.md`. The public-authored schema-17 transition adds only qualified logical-unit materialization and exact sealed-package binding in the existing queue, with execution explicitly unavailable; see `docs/logical-unit-materialization.md`. The public-authored schema-18 transition adds explicit complete-input activation, bounded evidence-reader v2, trusted dispatch exposure and fenced initial application through the existing executor; see `docs/complete-input-execution.md`. No production provider binding or trust policy is added. No supported plugin platform is included.
- Generated catalogs must be refreshed with `python scripts/generate_catalogs.py` and verified with `--check`.
- Treat all 0.x APIs as experimental. Preserve an already published contract ID and version payload; model breaking changes as a new contract version.
- Every contribution must carry a Developer Certificate of Origin sign-off.
- Open requested PRs in non-draft state, subscribe to their comments, and address, answer, and resolve actionable review threads. Require CI on the final PR commit. For this bootstrap, request one broad review and at most one focused rereview; do not merge, publish, or change repository visibility without owner authorization.
