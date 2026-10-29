# PR-05 agent source text view (owner decisions, 2026-10-22)

The owner approved four recommendations for an agent read of exact source text
and froze its contract (v1, kind `read_source_text`). These decisions extend
nothing beyond the source-text authority already recorded in
`pr-05-agent-source-text.md` (decisions 1 and 2).

1. **D1 — a separate request kind.** `read_source_text` is its own kind. Owner
   `hydrate_source`, `EvidencePin` and `evidence_metadata` stay exactly as
   shipped.
2. **D2 — delivered refs only.** An agent may read only a source ref that was
   delivered to it earlier in the same run.
3. **D3 — acceptance scope.** For the full-journey acceptance (IP05), agent
   hydration counts as satisfied by source text read through this view. Raw
   bytes and raw lineage stay owner-only by design. Nothing is recorded as
   closed until the view is built and the full journey actually runs.
4. **D4 — no new capability.** The view evaluates the full existing
   source-text authority check, not a scope-level capability test.

Not approved by these decisions: any wider grant or entitlement, raw or
package/part/native metadata for agents, a release, or a default-pin change.
See `../verification/pr-05-agent-source-text-view.md` for the implementation.
