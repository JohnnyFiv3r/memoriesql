# Public headless CLI

Status: implementation in this development branch; no release or production
source enrollment is claimed. Published `memoriesql==0.0.12` still has only the
contract-catalog commands.

The public package owns the single `memoriesql` executable. It is useful without
Desktop and never loads private Python modules or arbitrary plugins. `contracts`,
`contract` and `--version` keep their existing behavior. This cut adds:

| Command | Behavior |
| --- | --- |
| `doctor` | Reports installed version and whether local read inputs are configured. It contacts no database or model. |
| `capabilities` | Lists the static core command set and names unavailable product capabilities. |
| `inspect <bead-id>` | Uses the installed authorized stored-bead inspection; pending/thin/failed authorship remains distinct from accepted meaning. |
| `source <bead-id> --selection-file selection.json` | Reads one exact selection through the installed source-evidence reader. The file contains a `StoredEvidenceSelection`, including package and inventory pins. No source is inferred from a bead ID alone. |
| `relations <bead-id> [--known-at timestamp]` | Uses the installed authored/assessed relation reader. `known-at` requires an explicit UTC offset. |
| `sources enroll --request-file request.json` | Enrolls one exact, explicitly confirmed provider-neutral source through the installed authority adapter. |
| `sources grant --request-file request.json` | Grants bounded read/write permission for one enrolled source to a specified principal, subject to current authority and the existing membership/pairing checks. |
| `sources revoke --request-file request.json` | Terminally revokes one exact source with an actor-attributed reason and receipt. |
| `init --request-file request.json --secret-file PATH` | Operator-only, once per database: creates the single personal-local owner, workspace and an expiring owner credential through the existing bootstrap. Its secret is written once to a newly created owner-only file and never printed. |
| `clients pair --request-file request.json --secret-file PATH` | Pairs one local agent client with explicit capabilities, owned scopes and expiry. Its new secret is written once to a newly created owner-only file and never printed. |
| `clients revoke --request-file request.json` | Terminally revokes one pairing grant at its exact current revision. |

Bare `sources` reports `source_inventory_not_released`; there is no public
source-inventory reader yet. Provider-specific `sources connect` remains a
Desktop composition dependency, not an enrollment synonym.

The installed versioned command reference is
`memoriesql contract memoriesql.core-cli.v1 --json`; `capabilities --json`
reports its available command names and explicit pending capabilities. The
generated CLI catalog is derived from the reviewed public registry.

For `source`, create the selection from an authorized bead inspection's package
pin and the needed part ordinal. For example, the file shape is:

```json
{
  "package_id": "<inspected package UUID>",
  "inventory_sha256": "<inspected 64-character digest>",
  "part_ordinal": 0,
  "representation": "normalized",
  "offset": 0,
  "limit": 16384
}
```

The placeholders must be replaced with actual authorized pins; the CLI does not
discover or guess them. The installed reader validates the selection and
rechecks authority on every page.

For `sources enroll`, use the installed `EnrollExactSource` shape. For example:

```json
{
  "request_id": "<new request UUID>",
  "source_system": "<provider-neutral system key>",
  "installation_id": "<selected installation identity or omit>",
  "object_kind": "<exact source kind>",
  "external_object_id": "<exact selected object identity>",
  "source_schema_version": 1,
  "exact_source_confirmed": true
}
```

This confirmation records operator intent for that exact identity; it does not
prove discovery, validate a provider format, import historical sessions or
start capture. `sources grant` and `sources revoke` use the installed
`GrantExactSource` and `RevokeExactSource` shapes in
`memoriesql.source-enrollment.v1`, respectively. Their request UUIDs make an
identical retry idempotent; use a new UUID for a new action. Revocation is
terminal in v1. Grant creation alone does not activate capture or bypass
current target membership and pairing authority.

Each command accepts `--json`. Read commands serialize the same typed core
result for human and JSON use. Source authority commands return an `available`
envelope with their typed receipt, or an `unavailable`/`failed` reason. JSON is
compact and contains no branding. Exit status is 0 for `available`, 2 for
`unavailable` and 3 for failure or exhausted work. An unavailable operation
does not disclose partial content or reveal whether an ID was missing, revoked
or outside the caller's scope. Source content is printed only after a successful
exact authorized read; terminal output and shell history are therefore part of
the operator's disclosure decision.

Database-backed commands require `MEMORIESQL_DATABASE_URL`, `MEMORIESQL_LOCAL_CREDENTIAL` and
`MEMORIESQL_WORKSPACE_ID`. The credential is an existing authenticated local
secret; it is never accepted as a principal ID or echoed. The core reader derives
its hash, establishes current authorization inside its own short transaction,
and rechecks protected dependencies. A terminal, TTY or `--yes` grants nothing.
Missing/invalid configuration returns explicit unavailability. `doctor` checks
syntax/presence only; it is not proof that the database, grant or source is ready.
The source authority commands require that credential to represent a human with
the appropriate current management/share capabilities; they never take a
caller-supplied actor or workspace override. The CLI makes no model call.

`init` uses the installed `InitializePersonalLocal` shape in
`memoriesql.personal-local-initialization.v1`:

```json
{
  "request_id": "<new request UUID>",
  "owner_display_name": "<owner name>",
  "expires_at": "<UTC timestamp with offset>",
  "exact_initialization_confirmed": true
}
```

It needs only `MEMORIESQL_DATABASE_URL`, for an operator login that can assume
the application role; it creates the first credential rather than using one.
Every identifier derives from the request UUID. The database allows exactly one
owner: once initialized, a different request reports `already_initialized`. An
identical replay of the same request after an unknown outcome returns the same
receipt with `replayed: true`, issues no new credential and removes the new
`--secret-file`; the first run's secret file remains the owner's credential. An
existing secret file is refused as `stale_secret_file` and never read or
overwritten: after an interrupted run, rerun with a new path, and delete the old
file only if that replay reports nothing was initialized. If the connection
fails while the commit's outcome is unknown, `init` keeps the file and reports
`initialization_outcome_unknown`; recover the same way. A replay reporting
`replayed: true` means the kept file is the owner credential, and a fresh
initialization means it is not. The receipt carries
identifiers and expiry, including the workspace to export as
`MEMORIESQL_WORKSPACE_ID`.

The owner credential is for the owner only. Give each agent its own credential
with `clients pair`, adding a `sources grant` for any explicit scope it needs;
never hand an agent the owner's.
The owner credential expires at `expires_at`, after which owner commands report
unavailable; renewal is a separate operation not provided by this command.

`clients pair` uses the installed `PairLocalClient` shape in
`memoriesql.local-client-pairing.v1`:

```json
{
  "request_id": "<new request UUID>",
  "capabilities": ["memory.inspect", "memory.query", "source.read"],
  "access_scope_ids": ["<owned access scope UUID>"],
  "expires_at": "<UTC timestamp with offset>",
  "exact_pairing_confirmed": true
}
```

Only an authenticated human with the current `client.pair` capability can pair
or revoke a client. Capabilities must belong to the paired-agent role and scopes
must be active and owned by that human; refusal does not reveal which input
failed. A paired client reaches only its paired scopes, within its capabilities,
and each scope's policy decides the rest: an owner-private scope, such as the
personal-local default, admits it on the pairing human's behalf, while an explicit
scope, such as an enrolled exact source's, also needs a `sources grant` naming the
client's principal. Its authority is the intersection of pairing, role and current
grants. Principal, pairing, grant
and credential identifiers derive from the request UUID, so replaying a request
cannot create a second client.

The CLI creates `--secret-file` exclusively (never overwriting, never following a
symlink) with owner-only permissions before contacting the database, and stores
only the secret's SHA-256. It removes the file when pairing is refused or fails
before committing. If the connection fails while the commit's outcome is
unknown, it keeps the file and reports `pairing_outcome_unknown` with the derived
`principal_id` and `pairing_grant_id`. To recover, revoke that grant with
`clients revoke` (expected revision 1 and the requested capabilities and scopes),
delete the file, and pair again with a new request UUID. The secret is never
printed, logged or accepted as input. Whoever can read the file,
or the environment of a process given the secret, holds that client's authority
until expiry or revocation; supply it only through a channel the client cannot
use to read broader credentials. `clients revoke` takes the pairing grant,
expected revision, capabilities and scopes from the pairing receipt and records a
terminal revision; a changed revision is a conflict, not a success. A
revocation that reports `revocation_outcome_unknown` may have applied; retrying
it reports `pairing_revision_conflict` once it has.

This slice does not route Desktop's Textual interface or provider adapters. A
future, reviewed static service/client seam must preserve public core commands,
both installation orders and core-only usefulness without a core-to-Desktop
import. Provider-specific source connection and capture remain Desktop-owned;
PR-05 query/results/checkpoints and qualified recall transport retain
their release and qualification gates. No placeholder operation is reported as
successful.
