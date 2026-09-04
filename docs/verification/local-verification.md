# Local bootstrap verification

Date: 2026-09-04

The private staging branch was verified before its review commit. Build artifacts remain local and ignored; [`package-artifact-inventory.json`](package-artifact-inventory.json) records their exact members, sizes, SHA256 values, 49 contract identifiers, and 12 package APIs.

## Results

- Explicit registry: 49 records; default-deny; 43 JSON Schema, five Python, one synthetic connector.
- Boundary scan: no denied roots or private-boundary text.
- Unit/contract suite: 12 tests passed.
- Ruff: passed.
- mypy strict floor at Python 3.11: passed.
- `twine check --strict`: wheel and source distribution passed.
- Determinism: two builds were byte-identical after source-distribution normalization.
- Wheel: `34cd998f8c1ce5dd2aacbeeeb89a291fee2efabad4ae1a51521ac26fcead24cc`.
- Source distribution: `e0087975db108519a789e471a71a2eb6c567c996b7c0cbbf11bd75d15beee5b0`.

## Isolated interpreter matrix

The exact wheel was installed with `--no-index --no-deps` into clean virtual environments and exercised from outside the repository.

| Interpreter | Result |
| --- | --- |
| CPython 3.11.13 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.12.11 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.13.5 | 49 records; CLI lookup passed; isolation probe passed |
| CPython 3.14.0 | 49 records; CLI lookup passed; isolation probe passed |

The isolation probe denied home-directory lookup, filesystem walking, sockets, outbound connections, and subprocess launch. No database, validation-framework, ORM, or model-runtime dependency was imported.

Hosted GitHub checks remain the exact-head authority for the review branch. This local receipt is not a release or publication claim.
