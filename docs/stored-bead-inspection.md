# Stored bead inspection v1

Q's schema-24 forward slice stacks on P's verified schema-23 contract at
`b98d5cc79056547c18cfa51842f3d91373f48ad6`. It adds no canonical writes, model calls,
classification, relationship decisions, provider binding, release or private
consumption. P owns mention and classification writes and migrations 0022/0023.

`PostgresStoredBeadInspection.inspect(InspectStoredBead(bead_id=...))` returns the
immutable accepted identity, exact stored statements and unit-level support links,
local mentions, accepted type and vocabulary revision, and available accepted
classification contribution/provenance. Classifier confidence and distributions
are diagnostic judgments, not author confidence or truth. Optional rationale is
not synthesized. Statements retain their correction/supersession identities.
No titles or summaries are invented as replacements for authored meaning.

The accepted version's own receipt and successful apply operation establish
mentions capability. A complete revision-4/5 authorized result can have `mentions=[]`.
Legacy unsupported mentions/classification are null. Revision 5 cannot have an
empty accepted classification. No newest-task heuristic establishes acceptance.
Thin, pending, failed and accepted lifecycle states remain distinct; raw task
status accompanies lifecycle. An abstention receipt alone never becomes meaning.

The global resolution query selects the latest decision before authorization.
Protected decisions and absent global decisions both return `unavailable`, without
IDs, counts or status. This deliberately avoids exposing protected existence.
Authored local unresolved/ambiguous state remains separate. An older visible
resolution is never substituted. No partial candidate set is returned.

The application adapter owns a fresh transaction and current authorization context
per request. Source, statement, context and classification dependencies are checked;
the contribution is withheld as part of the whole bead if a prerequisite is
unavailable. All pinned optional classification context is conservatively checked,
even when it was not used as supporting evidence. It is never displayed as support.
Revoking producer/dispatch permission does not itself revoke a human's permission
to inspect already stored evidence. Source and model resource permissions do.
Every redisplay must call the reader again; callers must discard stale data after
an unavailable response and must not persist permission decisions in UI caches.

`read(ReadStoredBeadEvidence(...))` returns an exact bounded normalized-character
or retained-raw-byte page. The target package is unit-level evidence. Other packages
require a persisted classification support selection and the requested interval
must be contained within it. Inspected neighboring context alone is not a support
link. Original package hashes, part inventory, native facts and raw/fold derivation
identities remain intact. Reader `next_offset` means another storage page, never
incomplete source semantics. Use existing package inspect/inventory reads for
producer completeness declarations, native ordering and inventory; unknown
source topology remains unknown. No synthetic statement-to-span links are added.

Bounds: one bead, 32 statements, 64 unit support links per statement, 32 mentions,
64 authorized resolution candidates, 262144 canonical response bytes, 16384
normalized characters or 32768 raw bytes per page. Larger stored aggregates yield
`budget_exhausted` without truncation. The adapter sets a 2.5-second statement and
500ms lock timeout; timeout/lock exhaustion yields the same explicit bounded
outcome. Missing and protected resources return identical `unavailable` shapes.
Invalid selection shapes fail validation. Each read may create existing
authorization audit records, but never tasks, authored content, usage or author
exposure receipts.

Fictional installed-wheel tests cover accepted/empty/legacy/pending/failed states,
meaning qualifiers, diagnostic confidence, independent dispatch revocation,
source revocation between pages, hidden latest decisions, unrelated packages,
normalized/raw paging and absence of semantic side effects. These do not establish
live-provider quality or Desktop product readiness. Desktop consumption still
requires review, merged public dependencies and a separately authorized release.
