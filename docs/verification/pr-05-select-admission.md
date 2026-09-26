# PR-05 internal SQL admission qualification

This unreleased slice supplies a closed typed PostgreSQL AST binder/emitter and
an independent effective-privilege qualifier. It publishes no query results and
adds no migration, authorization kernel, candidate default, provider call or
investigation executor. The SQL recall operation catalog truthfully retains zero
executable entries. Its new logical metadata is generated solely from the public
registry, with the approved fields, semantic reference kinds and identity keys.

The approved interface and whole lifecycle dependency at `2baffc6` remain
byte-exact. Owner approval and matching content-free independent custody are
archived under `docs/approvals`. This branch depends on open/unmerged #46.
The subsequent relation-aware slice will explicitly consume PR-03's qualified
`2bacc4c949b0e7ec6a0485eb2a9b849e821f968c` head; migration 0030 belongs to
PR-03, and PR-05 reserves 0031 onward. It cannot substitute these mechanical
projection fixtures for the canonical lifecycle projector.

SQLGlot **30.19.0** is the proposed engineering pin now under qualification.
The selected pure Python distribution declares MIT, requires Python >=3.9 and
has no mandatory dependencies. Its own distribution retains the upstream
license; no source adaptation is included. The kernel refuses a different
version or native overlay. It uses the explicit PostgreSQL dialect, never an
optimizer, execution engine, second parser or unchecked original text.
[SQLGlot documentation](https://sqlglot.com/sqlglot.html).

Admission binds names/types independently to the installed generated catalog.
Semantic references keep their kinds through projections, sets and saved input
schemas; agent UUID casts cannot manufacture anchors. Values stay in driver
parameters. Unknown nodes/clauses fail closed without logging SQL or parameters.
It qualifies SELECT-only CTEs, correlated predicates, all six set forms, identity
inner/outer joins, groups/filters, explicit ROWS windows and one bounded native
CYCLE recursion. The emitted recursion independently repeats the depth guard
and requires qualified-root support edges. The internal correction-path hook
awaits PR-03 integration; it is not an executable installed capability.

PostgreSQL lacks native UUID MIN/MAX. The trusted emitter qualifies canonical
UUID-text comparison under C collation followed by a UUID cast, preserving the
semantic result kind; Boolean MIN/MAX uses BOOL_AND/BOOL_OR. Fictional native
tests exercise scalar, FILTER and window lowering, NULL/empty aggregate behavior,
bag multiplicities, peers, neighbors, cycles, diamonds and depths 1 through 8.
These are compiler-mechanics fixtures, not complete protected population witnesses
or result provenance. The emitted derivation program is not a disclosure receipt.

The privilege qualifier takes a trusted reviewed installation manifest, not an
agent-created allowlist. It inspects the real LOGIN, memberships, ownership,
database/schema/table/column/sequence/foreign-server grants, executable procedures
including PUBLIC, explicit and implicit default privileges, security-barrier
views, transitive physical dependencies and forced RLS policy fingerprints.
It pins exact server/OIDs/definitions and rejects downward SET ROLE. Stock
catalog metadata is not hidden. Its fictional capability fixture binds backend
PID/start; the eventual bridge must additionally bind the approved invocation,
run/frame/scope/transaction/policy and current canonical authorization.

The native fixture uses a real restricted login and a separate trusted issuer.
Statements bypassing admission still cannot write canonical/capability tables,
create objects/TEMP, elevate roles, use unsafe functions or widen the RLS scope
through a GUC. Revoking a capability withdraws its rows. Drift in policy,
procedure, view or grants invalidates qualification. This is independent
database-privilege evidence; parser admission is not its security boundary.

**Pending consequential fence:** stock PostgreSQL USERSET settings remain
changeable through direct SET by that login. This does not meet the approved
literal login-settings requirement. The exact native observation is preserved
in `pr-05-admission-failures.md`; owner amendment and matching custody
reconciliation, or a qualified server-side fence, are required before dependent
execution. No silent interpretation or policy relaxation is applied.

Initial local source checks: generated catalogs, byte-exact migration inventory,
public boundary, runtime closure, Ruff, strict mypy and 71 source tests pass after
the preserved dependency-allowlist correction. Focused native PostgreSQL 18.4
checks pass: 10 composition cases and 5 authority cases. One authority case
records the USERSET limitation rather than qualifying settings enforcement.
Installed wheel/sdist qualification on Python 3.13/3.14 and exact-head CI must
be recorded on the implementation PR before this slice is considered qualified.

Full immutable results/witnesses, canonical relation projections, operation
settlement/accounting, current authorization/erasure, restart, checkpoint/save/
restore and measured W1–W7 workload fit are subsequent integrated obligations.
No workload capacity, semantic quality, production candidate default or completed
PR-05 delivery is certified by these compiler/privilege tests.
