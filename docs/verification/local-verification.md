# Local bootstrap verification

Date: 2026-09-08

The bootstrap and its review corrections were verified as described below. Build artifacts remain local and ignored; [`package-artifact-inventory.json`](package-artifact-inventory.json) records the current artifacts' exact members, sizes, SHA256 values, 49 contract identifiers, and 12 package APIs.

## Results

- Explicit registry: 49 records; default-deny; 43 JSON Schema, five Python, one synthetic connector.
- Boundary scan: no denied roots or private-boundary text.
- Complete export provenance: 71 reviewed exports and 24 public-authored files, plus the manifest itself; every other repository file denied.
- Reviewed spike source-file hashes verified separately from canonical record-payload hashes; all 49 payloads unchanged.
- Initial unit/contract suite: 16 tests passed, including unregistered files, symlink records, unexpected generated outputs, and payload drift after a public-hash refresh. The review follow-up adds four focused regression tests.
- Ruff: passed.
- mypy strict floor at Python 3.11: passed.
- `compileall` for source, scripts, and tests: passed.
- `twine check --strict`: wheel and source distribution passed.
- Determinism: the initial two builds were byte-identical after source-distribution normalization; hosted CI independently compares two builds for each changed head.
- Current wheel: `17e09b1c985d5bb0b7dcd044773a7471cab14c8b9caebf76c961e48d0a1580ad` (18 members; 36,020 bytes).
- Current source distribution: `47aa5521497a55afb14a41dc9f8f20ba0d26b9ee04026a08de50637d094256b7` (30 members; 31,704 bytes).
- Complete, unmodified Apache License 2.0 text matched the upstream file byte-for-byte, including its appendix (SHA256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`). The explicit John Inniger trademark ownership statement remains separate from the source-code license.
- GitHub-rendered README HTML was inspected in a local browser with UTF-8 encoding; the approved banner loaded and all eight relative link/image references resolved. This is not a claim of public site availability.

The local builds used the pinned development tool environment with `build --no-isolation`; hosted package checks separately build in isolated environments. The artifact member sets, APIs, and catalog payloads are unchanged from the initial staging commit; completing the license appendix changed the license bytes and archive hashes. The later owner-approved canonical-repository correction changes project URLs to `JohnnyFiv3r/memoriesql`. Compared with the completed-license baseline, only wheel METADATA and RECORD bytes change; every installed package module and catalog remains byte-identical.

## Review corrections

- All eight catalog kinds, filenames, notes, counts, and include/empty dispositions are explicitly declared in the sole public registry. Five empty catalogs are no longer authorized by generator-only mappings.
- Provenance requires `Apache-2.0` for source/governance files and `trademark_asset_not_apache` for trademark-path assets, including public-authored files. Missing, arbitrary, or swapped license dispositions fail closed.
- Ten focused tests passed for these changes and the canonical-repository metadata, including malformed/missing/duplicate catalog declarations, an undeclared empty catalog, registry-driven wrapper metadata, and source/trademark license-denial cases.
- Changed Python files passed Ruff and strict mypy. The generated-catalog drift check confirms all eight catalog files are unchanged. Strict Twine checks and exact archive inspection passed for the changed-metadata artifacts.
- Unchanged runtime suites, local interpreter installations, and README rendering were not rerun merely to repeat evidence. The changed final head receives the complete hosted lane and exact-artifact Python matrix.

## Isolated interpreter matrix

The completed-license baseline wheel was installed with `--no-index --no-deps` into clean virtual environments and exercised from outside the repository. This local runtime evidence is retained because the current wheel's installed package modules and catalogs are byte-identical; hosted CI installs the exact current wheel independently.

| Interpreter | Result |
| --- | --- |
| CPython 3.11.13 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.12.11 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.13.5 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.14.0 | 49 records; CLI lookup passed; isolation probe passed |

The isolation probe denied home-directory lookup, filesystem walking, sockets, outbound connections, and subprocess launch. No database, validation-framework, ORM, or model-runtime dependency was imported. Inventory, exact-version lookup, invalid lookups, CLI JSON output, and CLI failure exit status were checked in each interpreter.

Hosted GitHub checks remain the exact-head authority for the review branch. This local receipt is not a release or publication claim.
