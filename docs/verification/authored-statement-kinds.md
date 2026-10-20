# Author-complete-unit revision 7: authored statement kinds (migration 0041)

## The defect

Revision 4 of `memory.semantic.author-complete-unit` is the authoring
revision a host runs today. Its response schema offers four statement kinds:
`observation`, `context`, `qualification` and `correction`.

No apply path accepts `correction` for a new note:
- the canonical statement rule requires a correction to name the statement it
  supersedes and a reason (`SemanticStatementDraft`);
- a new note refuses within-note corrections (`InitialBeadDraft`);
- the database backs both with its own refusals.

When the source has someone correcting a fact, the author may choose
`correction`. The output is then invalid. The unit's single attempt is spent
(`runtime.invalid_output`, retry class `never`), and the note is lost. The
binding task was cancelled at activation and cannot be activated again.

The response schema also never stated the render coverage rule, that every
authored statement appears in the rendered text or in its omissions. Notes were
lost to that rule too.

## The change

**Python.** A new task revision, 7, keeps revision 4's inputs, apply and render
semantics.
- Its output contract, `memory.semantic.local-mentions.output` revision 2,
  offers only `observation`, `context` and `qualification`. Its schema states:
  - "A correction heard in the source is an observation of what is now said to
    be true, with the earlier belief as context."
  - "Every authored statement appears in the note's rendered text or in its
    omissions."
- Its statements keep revision 4's canonical keys, with the supersession fields
  always null.
- It has its own input contract revision (2), author agent key
  (`memory.semantic.local-mentions-author.v2`), registry, activation command
  (version 6, schema 41) and apply command (version 8, schema 41).
- The shared statement, render and bundle models are untouched, so revisions
  2–6 keep their contract hashes.
- The executor, queue, worker and activation adapter recognize revision 7 beside
  revision 4.
- The public record is `memoriesql.local-entity-mentions.v2`, with its generator
  and registry entry.

Revision 7's pins:
- task contract `7ece3a75f9677667e0ef3f003c71ecfe131603cbc39fe94bb2ec2603daa3495e`;
- registry `semantic-tasks-v1:5b00443fe1872092d8bac8f20eab7ffd9412e53cf541471331e4aef5f7d51b0c`;
- output schema `360953819cc83cc29ccaaa629e4d505aab2e289c0618dd6e42f0a7cc9213c638`.

**Migration 0041** admits revision 7 beside revision 4:
- the two execution-revision CHECKs accept 7;
- an admission policy pins revision 7's task contract and registry;
- `activate_complete_input_v6` is revision 4's `activate_complete_input_v3`
  with revision 7's command version, schema, revision, operation and pins. It is
  granted, like v3, to the application role.
- Ten installed functions that treat revision 4 specially are restated with
  revision 7 handled exactly like it:
  - `semantic_task_input_reference_safe`;
  - `reauthorize_semantic_task`;
  - `consume_supervised_dispatch`;
  - `source_revisiting_authorize`;
  - `assert_complete_execution_binding`, including revision 7's task and
    registry pins;
  - `guard_complete_unit_execution`;
  - the apply body `apply_semantic_annotations_schema29`, with apply version 8,
    operation `complete_input.apply.v6` and revision 7's output pin;
  - `record_source_delivery_v1`;
  - `complete_input_exposure_valid`;
  - `inspect_stored_bead_v1`, which reads revision 7's mentions as it reads
    revision 4's.

Each restatement is generated from its latest installed text.
`test_authored_statement_kinds_migration` holds it to that text plus exactly the
listed edits. It also checks that nothing else is in the migration. Revisions
2–6, every stored statement and the PR-05 packet are unchanged. Migrations
0001–0040 keep their bytes.

**Not changed here.** A host still runs revision 4 until it pins revision 7. To
switch, it needs:
- the revision-7 task definition and registry;
- dispatch and claim policies naming revision 7;
- an author agent for `memory.semantic.local-mentions-author.v2`;
- activation through `activate_authored_mentions`.

## Tests

- `test_local_entity_mentions.test_a_correction_statement_loses_the_note_at_revision_4`
  reproduces the defect at revision 4. The author is offered all four kinds,
  chooses `correction`, and the attempt ends `terminal_failure` /
  `runtime.invalid_output` with no meaning written. It passes before and after
  this change, because revision 4 is unchanged.
- `test_authored_statement_kinds`, at schema 41:
  - a corrected fact is authored as an observation, with the earlier belief as
    context. The author was offered exactly three kinds, with both rules in the
    schema. The receipt names revision 7, and inspection shows its mention;
  - an output that carries `correction` anyway is refused, and nothing is
    written;
  - revision 4 still authors at schema 41.
- `test_authored_statement_kinds_contract` (database-free):
  - revision 7 offers three kinds;
  - `correction` fails as an enum value, before the supersession rule;
  - supersession metadata is refused;
  - revision 4's hashes are unchanged and it still offers four kinds;
  - stored history keeps `correction`;
  - a valid output has the same canonical form under revisions 4 and 7.
- `test_authored_statement_kinds_migration` (database-free) holds 0041 as
  above, and holds its pins to the Python contracts.
- The executor's installed tests run on schema 41.
