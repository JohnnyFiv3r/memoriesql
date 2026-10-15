# PR-05 agent source-text authority (owner decisions, 2026-09-28)

Two owner decisions set what an authorized agent may read about source units
through its own run-bound, receipted query results. The PR-05 coordinator relayed
both. Neither changes the approved packet (`78bbc04e…`), the lifecycle dependency
(`69a0d985…`), PR-03 authority or any wire shape, and `source.raw.read` stays
owner-only. Raw captured bytes, evidence packages, source revisiting and the
existing inspect/source/relations readers stay raw-gated and unavailable to
agents.

1. **Retained unit text** (at or before 17:15 CDT). An agent may read retained
   source-unit text and unit facts under M0006's source-unit row authority. It
   must hold `source.read` on the unit's event, plus `memory.query` on the bead's
   event and version and statement authorization.
2. **Package text** ("extend to package text"). An agent with that same current
   authority may receive the normalized text of its authorized units, whatever
   their storage representation. The bounds, as implemented:
   - Only the exact unit's pinned package is read, never unrelated parts,
     neighbouring units, a newer representation or the raw source reader.
   - Text is served only when every part is `producer_normalized`; identity (raw)
     or mixed packages leave the citation without text.
   - Package and revision bindings, content hashes and derivation are preserved.
     The disclosure receipt binds the exact delivered response.
   - Delivered text is labelled as a normalized projection, never as raw evidence
     or as sanitized, and the package's own declared coverage limits are
     surfaced. No raw-byte or clause-level citation guarantee is made.
   - Whole-result reauthorization, revocation and bounded delivery are unchanged.
     Reuse is refused after revocation.

The same sealed conversation-only package is used for authoring and for agent
reading. That package is the Desktop lane's
`desktop.claude-code.conversation-records.v1` projection. Its versioned
derivation and raw lineage are kept as noncontent dependency provenance; raw
lineage stays owner-only.

Implementation and proofs: M0037 at commit `9a396dd95ba0c2f50d1e2e160070d88854fab148`; see
`../verification/pr-05-public-results.md`. The decisions authorize this
implementation only, not a release or additional live calls.

Custody: a content-free notice for the independent custodian asks whether ea-3
(`12e802be…`) still stands for this authority change. The implementer cannot
decide that, because it depends on frozen conditions the implementer must not
see.
