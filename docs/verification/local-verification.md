# Local bootstrap verification

Date: 2026-09-08

The private staging branch was verified before its integrity-correction commit. Build artifacts remain local and ignored; [`package-artifact-inventory.json`](package-artifact-inventory.json) records their exact members, sizes, SHA256 values, 49 contract identifiers, and 12 package APIs.

## Results

- Explicit registry: 49 records; default-deny; 43 JSON Schema, five Python, one synthetic connector.
- Boundary scan: no denied roots or private-boundary text.
- Complete export provenance: 71 reviewed exports and 24 public-authored files, plus the manifest itself; every other repository file denied.
- Reviewed spike source-file hashes verified separately from canonical record-payload hashes; all 49 payloads unchanged.
- Unit/contract suite: 16 tests passed, including unregistered files, symlink records, unexpected generated outputs, and payload drift after a public-hash refresh.
- Ruff: passed.
- mypy strict floor at Python 3.11: passed.
- `compileall` for source, scripts, and tests: passed.
- `twine check --strict`: wheel and source distribution passed.
- Determinism: two builds were byte-identical after source-distribution normalization.
- Wheel: `fc076ce88a83ca13e9cc7ca987f44f847d74b3ee9b50a70b175ef4733c367822` (18 members; 36,010 bytes).
- Source distribution: `f9e4c9bddabe05ad1f920a4dff8c1b89830131878e5af01f6cf96f91cf81b9bf` (30 members; 31,696 bytes).
- Complete, unmodified Apache License 2.0 text matched the upstream file byte-for-byte, including its appendix (SHA256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`). The explicit John Inniger trademark ownership statement remains separate from the source-code license.
- GitHub-rendered README HTML was inspected in a local browser with UTF-8 encoding; the approved banner loaded and all eight relative link/image references resolved. This is not a claim of public site availability.

The local deterministic builds used the pinned development tool environment with `build --no-isolation`; hosted package checks separately build in isolated environments. The artifact member sets, APIs, and catalog payloads are unchanged from the initial staging commit; completing the license appendix changed the license bytes and archive hashes.

## Isolated interpreter matrix

The exact wheel was installed with `--no-index --no-deps` into clean virtual environments and exercised from outside the repository.

| Interpreter | Result |
| --- | --- |
| CPython 3.11.13 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.12.11 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.13.5 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.14.0 | 49 records; CLI lookup passed; isolation probe passed |

The isolation probe denied home-directory lookup, filesystem walking, sockets, outbound connections, and subprocess launch. No database, validation-framework, ORM, or model-runtime dependency was imported. Inventory, exact-version lookup, invalid lookups, CLI JSON output, and CLI failure exit status were checked in each interpreter.

Hosted GitHub checks remain the exact-head authority for the review branch. This local receipt is not a release or publication claim.
