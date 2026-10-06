# Inspection withholding (migration 0043)

Owner decisions 6 and 8 of 2026-10-06 cover stored-bead inspection
(`inspect_stored_bead_v1`, CLI `inspect`) and its evidence reader
(`read_stored_bead_evidence_v1`, CLI `source`). Migration 0043 restates both
functions from their installed text (0041 and 0024) with exactly the edits
that `tests/unit/test_inspection_withholding_migration.py` lists. The record
shapes and outcomes are unchanged.

## The defects

- **Inspection showed what queries withhold (decision 8).** The query
  population withholds a whole note family when any member is unreadable: a
  note, plus the supersession neighbours of its accepted version. Inspection
  never consulted the neighbours. A note whose correction the reader could
  not read was therefore missing from query results but whole in inspection.
- **A late denial left a trace (pair 3).** Inspection authorized sources one
  by one while walking a note. Each authorization wrote an "allowed"
  `authorization_audit_events` row. A denial further on returned
  `unavailable` normally, so those rows stayed, where an unknown note leaves
  none. The evidence reader did the same whenever it refused after the note's
  own inspection had succeeded, for example on a selection that supports
  nothing in the note.
- **A note the reader cannot read reported its size (pair 4).** The
  statement budget (more than 32 statements under the watermark) was decided
  before the statements' evidence and context sources were authorized. The
  classification contribution's checks, which can deny the whole note, ran
  only after the statement, link and mention budgets. An unreadable note over
  budget therefore reported `budget_exhausted`, where an unknown note reports
  `unavailable`.
- **Context sources (the approved context-source fix).** A context source is a
  unit of the statement's own evidence. Inspection authorized the unit ID as
  if it were a source object, so every note with a context source inspected as
  `unavailable`.

## The change

- **Withholding.** An accepted note must pass the population's own record
  reads (`query_bead_records_v1`), for itself and for every supersession
  neighbour of its accepted version, at the time of the request. Otherwise
  the note reads exactly as one that does not exist. The evidence reader
  inspects the note before and after each page, so it withholds alike.
- **No trace.** Every denial and every budget now raises inside the
  function's block. That block rolls its audit rows back, and the handler
  returns the same `unavailable` or `budget_exhausted` record as before. The
  evidence reader's refusals after its first inspection, and its budget,
  behave the same way. Audit rows persist only for a read that succeeds.
- **Authorize before any budget.** These checks run before the first budget:
  - every statement under the watermark;
  - every source that its evidence and context name;
  - the classification contribution's checks, moved unchanged.

  Later checks only repeat them. The mentions' own checks never deny the note:
  an unreadable resolution decision is reported per mention, as before. The
  response-size and time budgets now follow the return boundary's
  revalidation.
- **Context sources** are authorized through their unit's event's source
  object, as evidence is. A missing or unreadable context source reads exactly
  as an unknown note.

One further consequence: the evidence reader's handler now maps every program
limit inside it to `budget_exhausted`. That includes the raw-page chunk work
bound, which previously escaped as an unmapped error.

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
- `test_authored_statement_kinds.test_a_note_with_a_context_source_is_inspectable`:
  a note whose statement names a context source is inspectable. Once that
  source is revoked, the note reads exactly as an unknown note.
- `test_inspection_withholding_migration` (database-free) checks that 0043:
  - restates the two functions with exactly the listed edits, and creates
    nothing else;
  - grants nothing;
  - leaves only the handler returning a bare refusal or budget;
  - places every check that can deny the note before the first budget.

The existing suites stay green: `test_stored_bead_inspection`,
`test_authored_statement_kinds`, `test_declared_evidence_scope` and
`test_installed_migrations`.
