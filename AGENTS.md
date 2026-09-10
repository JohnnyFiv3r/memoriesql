# Contributor guidance

This repository is the staged public boundary for the memoriesQL open core. Keep changes provider-neutral, deterministic, content-blind, and default-deny.

- `contracts/public-registry.json` is the sole authority for generated catalogs.
- Never discover export candidates by scanning another repository.
- Do not add desktop UI, installers, update channels, product module manifests, provider-specific discovery or parsing, private evidence, owner data, or credentials.
- The approved migration substrate is limited to the explicit migration inventory, runner, schema inspection and resource closure. Preserve SQL 0001–0014 byte-for-byte. The runtime cut is limited to `contracts/runtime-inventory.json`: neutral runtime and internal static mechanics, with caller-supplied composition. The public-authored schema-15 transition adds only the inventoried immutable-observation commands, task definitions and adapter dispatch. No production provider binding or supported plugin platform is included.
- Generated catalogs must be refreshed with `python scripts/generate_catalogs.py` and verified with `--check`.
- Treat all 0.x APIs as experimental. Preserve an already published contract ID and version payload; model breaking changes as a new contract version.
- Every contribution must carry a Developer Certificate of Origin sign-off.
- Open requested PRs in non-draft state, subscribe to their comments, and address, answer, and resolve actionable review threads. Require CI on the final PR commit. For this bootstrap, request one broad review and at most one focused rereview; do not merge, publish, or change repository visibility without owner authorization.
