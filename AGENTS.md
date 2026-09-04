# Contributor guidance

This repository is the staged public boundary for the memoriesQL open core. Keep changes provider-neutral, deterministic, content-blind, and default-deny.

- `contracts/public-registry.json` is the sole authority for generated catalogs.
- Never discover export candidates by scanning another repository.
- Do not add desktop UI, installers, update channels, product module manifests, provider-specific discovery or parsing, private evidence, owner data, or credentials.
- Do not add migrations until their separately audited extraction is approved.
- Generated catalogs must be refreshed with `python scripts/generate_catalogs.py` and verified with `--check`.
- Treat all 0.x APIs as experimental. Preserve an already published contract ID and version payload; model breaking changes as a new contract version.
- Every contribution must carry a Developer Certificate of Origin sign-off.
