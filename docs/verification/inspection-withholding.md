# Inspection withholding (migration 0043)

Owner decisions 6 and 8 of 2026-10-06, and decision 7 as the owner extended it
on 2026-10-07, cover two inspection readers:
- stored-bead inspection (`inspect_stored_bead_v1`, CLI `inspect`) and its
  evidence reader (`read_stored_bead_evidence_v1`, CLI `source`);
- relation inspection (`inspect_bead_relations_v2` and `_v3`, CLI
  `relations`, and the legacy `_v1`), built on `inspect_bead_relations_v3_base`
  and `relation_inspection_frame_v1`.

Migration 0043 restates those functions from their installed text (0041, 0024
and 0030) with exactly the edits that
`tests/unit/test_inspection_withholding_migration.py` lists. The record shapes
and outcomes are unchanged.

## The defects

- **Inspection showed what queries withhold (decision 8).** The query
  population withholds a whole note family when any member is unreadable: a
  note, plus the supersession neighbours of its accepted version. Inspection
  never consulted the neighbours. A note whose correction the reader could
  not read was therefore missing from query results but whole in inspection.
- **A late denial left a trace (pair 3).** Both readers authorized sources one
  by one while walking. Each authorization wrote an "allowed"
  `authorization_audit_events` row. A denial further on returned `unavailable`
  normally, so those rows stayed, where an unknown bead leaves none.
  - Stored-bead inspection was denied this way on a later source.
  - The evidence reader did the same whenever it refused after the note's own
    inspection had succeeded, for example on a selection that supports nothing
    in the note.
  - Relation inspection did the same when a listed relation's other endpoint,
    closure, pair or task was unreadable, and when its v1 or v2 wrapper
    refused after the frame had succeeded.
- **A bead the reader cannot read reported its size (pair 4).**
  - Stored-bead inspection decided its statement budget (more than 32
    statements under the watermark) before the statements' evidence and
    context sources were authorized. The classification contribution's
    checks, which can deny the whole note, ran only after the statement, link
    and mention budgets. An unreadable note over budget therefore reported
    `budget_exhausted`, where an unknown note reports `unavailable`.
  - Relation inspection decided its response-size and time budgets in the
    base, before the frame authorized each relation's closure and the
    reader's current authority.
- **Context sources (the approved context-source fix).** A context source is a
  unit of the statement's own evidence. Inspection authorized the unit ID as
  if it were a source object, so every note with a context source inspected as
  `unavailable`.
- **A revision-6 relation showed text about a candidate its reader could not
  read (decision 7, extended on 2026-10-07).** A revision-6 author is shown
  every candidate supplied to it, so its relation's rationale or
  qualification may quote any of them. Inspecting the authoring bead required
  every supplied candidate, through its candidate assessments. Inspecting the
  relation's target required only the relation's own endpoints, evidence and
  closure. So once a candidate other than the endpoints became unreadable, the
  target still showed the relation's text.

## The change

- **Withholding.** An accepted note must pass the population's own record
  reads (`query_bead_records_v1`), for itself and for every supersession
  neighbour of its accepted version, at the time of the request. Otherwise
  the note reads exactly as one that does not exist. The evidence reader
  inspects the note before and after each page, so it withholds alike.
- **No trace.** Every denial and every budget now raises inside the
  function's block. That block rolls its audit rows back, and the handler
  returns the same refusal record as before. This applies to:
  - stored-bead inspection;
  - the evidence reader's refusals after its first inspection, and its
    budget;
  - the relation base, the frame, and the v1 and v2 wrappers, which gain
    handlers that return their unchanged refusal records.

  Audit rows persist only for a read that succeeds.
- **Authorize before any budget.**
  - In stored-bead inspection, these checks run before the first budget:
    - every statement under the watermark;
    - every source that its evidence and context name;
    - the classification contribution's checks, moved unchanged.

    Later checks only repeat them. The mentions' own checks never deny the
    note: an unreadable resolution decision is reported per mention, as
    before. The response-size and time budgets follow the return boundary's
    revalidation.
  - In relation inspection, the base's response-size and time budgets move to
    the frame's end, after every check. The size bound is unchanged at
    512 KiB. The two-second time budget now runs from the statement's start
    and covers the whole inspection, not only the base. A derivation lineage
    over its limit is still reported as `budget_exhausted`, never truncated.
- **Context sources** are authorized through their unit's event's source
  object, as evidence is. A missing or unreadable context source reads exactly
  as an unknown note.
- **Every supplied candidate, from either endpoint.** An authored relation is
  disclosed only to a reader currently authorized for every candidate
  supplied to its author.
  - The base checks each supplied candidate's version for every listed
    authored relation.
  - The frame retains and re-reads each candidate's records as a dependency.
  - Otherwise the entire relation is withheld. The whole read is then exactly
    a read of missing data, its audit trace included.
- **Private operational diagnostics.** Each such withholding writes one line to
  the PostgreSQL server log, for operators. It names the relation, the
  principal and the unreadable candidate by identifier, and carries no text.
  Both functions pin `client_min_messages`, so the line never reaches a client
  connection, even one that asks for log messages. The server log is an
  operator-only channel. Its lines may name identifiers the caller may not
  see, which is why they never reach a client connection. No exception
  message, audit row or notice carries the reason: callers see only the
  refusal they would see for a missing bead.

One further consequence: the evidence reader's handler now maps every program
limit inside it to `budget_exhausted`. That includes the raw-page chunk work
bound, which previously escaped as an unmapped error.

For assessed relations, decision 7 already held in relation inspection. Every
endpoint and basis bead of an assessed relation is pinned to its task, and the
task disposes of every pinned pair. Inspection lists each pair that involves
the inspected bead and authorizes both beads. So a bead shows an assessed
relation's author text only to a reader of the task's whole pinned population.
No change was needed for that.

Migrations 0001–0042 keep their bytes.

## Tests

Each runtime test below failed before the change and passes after it.
- In `test_agent_sql_results`, at schema 43:
  - `test_inspection_withholds_what_the_population_withholds`: a note whose
    correction's source is revoked is missing from query results, and
    inspection now returns exactly what it returns for an unknown note.
  - `test_a_late_inspection_denial_leaves_the_trace_of_an_unknown_note`: a
    note denied on a second source, after its first source was authorized,
    now writes the same audit rows as an unknown note (none).
  - `test_an_unreadable_oversized_note_never_reports_its_size`:
    - a note with 33 statements reports `budget_exhausted` to a reader of the
      whole note;
    - once its context source is revoked, it reports exactly what an unknown
      note reports;
    - the population's reads never consult context, so this isolates the
      order of authorization.
  - `test_a_refused_evidence_read_leaves_the_trace_of_an_unknown_note`: an
    evidence read refused after the note's own inspection now writes the same
    audit rows as one for an unknown note (none).
  - `test_a_late_relation_inspection_denial_leaves_the_trace_of_an_unknown_bead`:
    a relation inspection denied on the relation's revoked target, after the
    inspected bead's own source was authorized, now writes the same audit rows
    as an unknown bead (none).
- `test_authored_relations.test_an_authored_relation_needs_every_candidate_its_author_was_supplied`:
  - fully authorized, both endpoints show the relation with its text, through
    v2 and the legacy v1;
  - once a supplied candidate that is neither endpoint is revoked, both
    endpoints read exactly as a missing bead, audit trace included. Before the
    change, the target still showed the text;
  - a caller's connection that asks for server log messages hears nothing of
    the withholding.
- `test_authored_statement_kinds.test_a_note_with_a_context_source_is_inspectable`:
  a note whose statement names a context source is inspectable. Once that
  source is revoked, the note reads exactly as an unknown note.
- `test_inspection_withholding_migration` (database-free) checks that 0043:
  - restates the six functions with exactly the listed edits, and creates
    nothing else;
  - grants nothing;
  - leaves only the handlers returning a bare refusal or budget;
  - places every check that can deny a bead before the first budget;
  - logs only the two revision-6 withholdings, from functions that pin
    `client_min_messages`.

`test_relation_inspection_needs_the_whole_task_of_a_relation_it_shows` pins
decision 7's existing hold in inspection. A raw reader of the endpoints' scope
inspects a relation's target and gets exactly what it gets for an unknown
bead, while the owner reads the relation's text. It passes before and after
the change.

The existing inspection suites stay green: `test_stored_bead_inspection`,
`test_authored_statement_kinds`, `test_declared_evidence_scope` and
`test_installed_migrations`. Three suites were rerun with their final
migration hop retargeted: PR-03's relation-assessment and assessed-lifecycle
suites, and the revision-6 suite. Each gives identical per-test outcomes at
schema 42 and at schema 43, 87 tests each. With the revision-6 check added, the
same suites at schema 43 again give identical outcomes. The one exception is
the new revision-6 test, which migrates inside the test and so errors by
construction when its suite is retargeted.
