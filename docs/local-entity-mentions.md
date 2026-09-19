# Authored local mentions

Schema 22 adds local entity mentions to the existing author-controlled source
revisiting path. Activation version 3 selects author-complete-unit task revision 4;
apply version 5 accepts its distinct output contract. Older contracts and SQL
migration files retain their bytes and meanings.

Every authored bead requires `mentions`, a collection of at most 32 items; an empty
collection is valid. Each item has an opaque UUID `entity_mention_id`, nonblank
`surface_text` of at most 1024 characters, `local_identity_state` of `unresolved` or
`ambiguous`, and optional nonblank `local_identity_reason` of at most 1024
characters. Ambiguity requires a reason. Names never identify canonical entities.
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
