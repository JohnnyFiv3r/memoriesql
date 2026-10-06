# Authored local mentions

Schema 22 adds local entity mentions to the existing author-controlled source
revisiting path. Activation version 3 selects author-complete-unit task revision 4;
apply version 5 accepts its distinct output contract. Older contracts and SQL
migration files retain their bytes and meanings.

Every authored bead requires `mentions`, a collection of at most 32 items; an empty
collection is valid. Each item has an opaque UUID `entity_mention_id`, nonblank
`surface_text` of at most 1024 characters, `local_identity_state` of `unresolved` or
`ambiguous`, and optional nonblank `local_identity_reason` of at most 1024
characters. The entire canonical JSON output is additionally limited to 12,000
UTF-8 bytes, matching SQL acceptance; individual field maxima cannot all be used
simultaneously. Ambiguity requires a reason. Names never identify canonical entities.
No global entity/candidate/resolution records are created.

Acceptance writes existing `entity_mentions` rows in the same transaction as
statements, render, task receipt and settlement. The existing bead-version foreign
key binds each mention to its event and source unit; the accepted task's original
package pin supplies support provenance. Both offsets remain NULL. No character
spans, finer statement links, or evidence of comprehension are invented.

The new nullable columns preserve legacy rows. Local authored uncertainty remains
immutable and separate from later governed resolution. The accepted-semantics seal
now rejects additional entity mentions after acceptance. Replay uses the unchanged
command receipt mechanism and preserves the original mention IDs and set.

Inspection must use the accepted bead version's own semantic task receipt and
successful apply linkage to determine mention capability. A fully authorized read
of task revision 4 can report authored-empty. Legacy/null receipt lineage,
unavailable or hidden content, pending tasks and failed acceptance do not imply an
empty authored set. Q owns the read projection and current authorization of later
governed resolution, including hidden-versus-unresolved handling.

No provider, production activation, migration deployment, global resolution,
private consumption or live qualification is included. Source delivery and unique
coverage retain their existing revisiting semantics; a successful delivery does
not prove comprehension or fidelity.

## Revision 7: corrections within a note

Schema 41 adds author-complete-unit task revision 7, activated by
`activate_complete_input_v6` (activation version 6) and applied by apply version
8. Its inputs and render semantics are revision 4's. Its output contract,
`memory.semantic.local-mentions.output` revision 2, lets a correction supersede
an earlier statement of the same note.

Revision 4's schema offers `correction`, but no apply path accepts it for a new
note: a new note refuses within-note corrections, and the complete-input path
refuses every correction. An author that chose it returned invalid output, its
unit's one attempt was spent, and the note was lost.

In revision 7, when the source corrects something the note states, such as a
count said and then corrected:
- The earlier statement is kept as it was said. It keeps the prior meaning.
- The correction is a statement of kind `correction`. It names the earlier
  statement in `supersedes_statement_id` and gives the reason in
  `correction_reason`. A statement has at most one correction, and a correction
  may itself be corrected.
- The note's render covers exactly the statements no correction supersedes. A
  superseded statement is neither rendered nor omitted, as the render trigger
  requires.
- Apply writes the existing statement supersession. Inspection and the query
  surface show both statements, with the correction's target and reason.

Revision 7's schema states both rules:
- A correction of something this note states supersedes that earlier statement:
  it names the statement in `supersedes_statement_id` and gives the reason in
  `correction_reason`. The superseded statement keeps the prior meaning.
- Every statement that no correction supersedes appears in the note's rendered
  text or in its omissions, and a superseded statement appears in neither.

A correction whose target is not an earlier statement of its note is refused.
So is a second correction of one statement. The output contract refuses them
first, and apply refuses them again (SQLSTATE 22023). Either way nothing is
written. Revision 7's statements keep revision 4's canonical keys. Stored
statements of every historical kind read as before.

A correction of something an earlier, stored note says is not a within-note
correction. Revision 7 does not link a new note to an earlier one.

Revision 7 has its own task contract, registry, admission policy and author agent
key (`memory.semantic.local-mentions-author.v2`). So a worker runs it only from
the revision-7 registry, and a dispatch policy and claim policy must name
revision 7. Revision 4 is unchanged and remains available. The public record is
`contracts/records/memoriesql-local-entity-mentions-v2.json`.
