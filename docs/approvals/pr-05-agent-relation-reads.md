# PR-05 agent relation reads (owner decision AM-5, 2026-09-28)

John approved AM-5 at 23:55 CDT on 2026-09-28, and the PR-05 coordinator relayed
it. AM-5 decides the open PR-03 question of how paired agents read assessed
relations. It changes none of these:

- the approved packet (`78bbc04e…`) or its relation catalog;
- the lifecycle dependency (`69a0d985…`) or PR-03's lifecycle projection;
- any wire shape.

`source.raw.read` stays owner-only.

**Clarifies** `docs/relation-assessment.md` lines 250–255: "Reads use current
`memory.query` and source-read authority over their entire disclosed dependency
closure". PR-03's read path required raw revisiting authority instead, so paired
agents saw `relation_tables` / `source_raw_read_required`.

**Rule (AM-5, verbatim).**

- A paired agent holding memory.query and source.read over EVERY member of an
  assessed relation's disclosed dependency closure may read that relation: its
  label (predicate revision), direction, endpoints, basis kind, attribution,
  acceptance and lifecycle state, and its evidence unit references.
- Raw-source provenance records stay owner-only.
- Any unreadable dependency makes the whole relation unavailable
  (non-disclosing).
- Withdrawn assertions are never eligible, disputed ones keep their uncertainty,
  and current/as-of is consistent across inspection and retrieval.

**Technical refinement.**

1. **Closure.** The disclosed dependency closure is exactly the one PR-03's read
   path already authorizes:
   - endpoint and basis beads, versions and statements;
   - every evidence event;
   - lifecycle evidence, corrections and replacements;
   - the beads and units that the relation's root status is computed from.

   The agent's read applies the owner's checks, with one substitution: where the
   owner's read requires raw revisiting authority over a source object, the
   agent's read requires `source.read` on the same event. Authorization covers the
   whole closure, including the parts that item 3 withholds from agents.
2. **Visible rows.** The approved relation catalog fixes each table's columns,
   most of them non-null. A relation row therefore cannot be disclosed with only
   some of its columns. An authorized agent receives whole rows with the owner's
   values at the same frame in these tables: `assessed_relations`,
   `relation_statements` (endpoint and basis statements, the attribution),
   `relation_evidence`, `relation_types`, `relation_corrections` and
   `relation_replacements`.
   Beyond the rule's list, those rows also carry:
   - the assertion's rationale, qualification and author confidence;
   - its task, run and acceptance-receipt references;
   - the type's pinned definition;
   - its root status and independent root count.
3. **Owner-only history.** `relation_events`, `relation_event_evidence` and
   `relation_pairs` carry governance reasons, recording principals, cited event
   evidence and task coverage. They stay owner-only. An agent's result discloses
   them as a coverage gap, never as silent absence. The lifecycle outcome still
   reaches agents through `state`, `support_eligible`, `support_reason` and
   `correction_pending`.
4. **Raw-source provenance.** Root identities are source-object UUIDs and are not
   disclosed: no relation column carries one. Raw bytes and parts, raw lineage
   and source revisiting stay owner-only.
5. **Eligibility.** State, support eligibility, head, corrections and root status
   come from PR-03's single projection. Withdrawn assertions have
   `support_eligible = false`. Recursive path support additionally requires
   qualified roots.
6. **Unchanged.** These keep their existing authority and stay unavailable to
   agents:
   - governed confirm, dispute and retract;
   - relation-assessment activation;
   - the `relations` inspection reader.

   Returned relation rows receive no hydration or exact-source credit merely
   because they are readable.
7. **Agent SQL results.** In a fresh query, a relation whose disclosed closure
   the agent cannot wholly read is absent from every agent-visible relation
   table. No gap, count, type pin or history is disclosed for it, and the query
   itself stays available with the other rows. Re-disclosure of a saved result
   after a closure member lapses is `unavailable`. The `relations` inspection
   reader stays owner-only.

**Implementation and proofs:** migration 0039; see
`../verification/pr-05-public-results.md`. This decision authorizes that
implementation only, not a release or additional live calls.

**Custody:** a content-free notice asks the independent custodian two questions:
whether ea-3 (`12e802be…`) and ea-4 (`d118ded2…`) stand for this authority
change, and what reconciliation it requires.
