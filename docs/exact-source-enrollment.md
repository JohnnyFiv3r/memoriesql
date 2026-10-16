# Exact source enrollment

The `memoriesql.source-enrollment.v1` contract and migration 0036 provide a
provider-neutral authority step for one explicitly selected source. It is merged
after migration 0035 and prepared for the proposed 0.0.13 release; it is not a
published capability in `memoriesql==0.0.12`. It does not discover local sources, read files, parse a
provider format, capture bytes, pair a device, start a worker, or activate the
private product UI. No source is enrolled merely because it is discoverable.

An authenticated human with current `workspace.manage`, `source.manage` and
`source.share` capabilities submits `EnrollExactSource` with
`exact_source_confirmed=true`, a
fresh request UUID, a source system, optional installation identity, object
kind, opaque external object identity, and schema version. The database derives
tenant, workspace, actor, and owner from the credential context. It creates one
new explicit access scope, gives the enrolling human a scoped read/write/share
grant, and binds one protected source resource to one source object. It stores
no source bytes. Repeating the identical request UUID and identity returns the
same source and scope; a changed request or duplicate natural identity does not
claim or reveal an existing source. A caller's confirmation is an operator
intent signal, not proof that a provider adapter found or validated the object.

`GrantExactSource` binds the existing access-grant operation to that one source.
It permits only `read`, `write`, or both, and records the target principal and
expiry. The target still needs an active workspace membership, role capability,
and, for a paired client, a current pairing grant that includes this exact
scope. Grant creation alone never starts capture. Revoking the source marks its
protected resource inactive and writes an immutable, actor-attributed receipt;
subsequent protected source reads and capture authorization fail. Replaying the
same revocation request returns the recorded outcome. Revocation is terminal in
this version; reconnecting a revoked natural identity requires a separately
reviewed reactivation operation, rather than a silent new identity.

The typed application requests and PostgreSQL adapter live in
`memoriesql.application.source_enrollment` and
`memoriesql.infrastructure.postgres.source_enrollment`. Product connections
must use the installed adapter with an authenticated credential and an idle
connection that the adapter can own for its short transaction. The installed
contract catalog describes the request and receipt shapes. The public CLI
binds these operations as `sources enroll|grant|revoke --request-file` and
returns typed receipts; it does not discover or validate provider objects. A
future Desktop binding must preserve the exact native selection and recheck
current authority before every capture/read; this migration does not authorize
access to historical sessions or outbound model disclosure.
