-- One goal bead type in place of goal_declaration and goal_update, and a
-- social bead type.
--
-- A goal and the updates about it are one kind of statement. How an update
-- relates to the goal it updates is a relationship between the two beads
-- (the update replaces, confirms or withdraws what was said of the goal, or
-- follows from it), which the Core Memory engine's judge records; a type
-- cannot say it. Read by a model, the two types were not told apart
-- reliably: in the engine's reader probe (Core-Memory eval/reader-probe,
-- 2026-11-19), one of three updates was typed goal_update.
--
-- goal is registered at revision 1, with a definition that joins the two.
-- goal_declaration and goal_update each get a revision 2 with their
-- definitions unchanged and not authorable, so neither is a current bead
-- type any more: current_bead_type_revisions, and so engine_vocabulary_v1,
-- no longer offer them, and engine intake refuses a type decision for them.
-- Their revision-1 rows are unchanged. Beads that pin them keep their
-- meaning, and an author that pins revision 1 explicitly is accepted, as for
-- any pinned revision.
--
-- social is for what keeps people in touch: a greeting, thanks, compliment,
-- sympathy or check-in that states nothing else. Without it, a model left
-- such a message untyped, as no type fit it, though tending a relationship
-- is sometimes what a conversation is about. A message that also tells
-- something takes the type of what it tells: in the probe of 2026-11-20 the
-- reader typed 14 of 14 such messages social and 9 of 10 statements by what
-- they stated.
--
-- Forward-only; earlier migrations keep their bytes.

INSERT INTO memoriesql.bead_types (
    bead_type_id, stable_key, introduced_in_schema_version
) VALUES
    ('10000000-0000-4000-8000-00000000000d', 'goal', 60),
    ('10000000-0000-4000-8000-00000000000e', 'social', 60);

INSERT INTO memoriesql.bead_type_revisions (
    bead_type_revision_id,
    bead_type_id,
    revision,
    display_name,
    definition,
    authorable,
    introduced_in_schema_version
) VALUES
    ('11000000-0000-4000-8000-00000000000d', '10000000-0000-4000-8000-00000000000d', 1, 'Goal', 'An explicit observed desired future state or objective, or an observed update about one: its progress, scope, status, blocking, completion, or abandonment', true, 60),
    ('11000000-0000-4000-8000-00000000000e', '10000000-0000-4000-8000-00000000000e', 1, 'Social', 'A greeting, thanks, compliment, sympathy or check-in that tends a relationship and states nothing else. A message that also tells something takes the type of what it tells', true, 60),
    ('11000000-0000-4000-8000-000000000203', '10000000-0000-4000-8000-000000000003', 2, 'Goal declaration', 'An explicit observed desired future state or objective', false, 60),
    ('11000000-0000-4000-8000-000000000204', '10000000-0000-4000-8000-000000000004', 2, 'Goal update', 'An observed progress, scope, status, blocking, completion, or abandonment update about a goal', false, 60);
