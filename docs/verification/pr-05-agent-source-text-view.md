# PR-05 agent source text view (`read_source_text`)

**Primary claim: a paired agent can read a bounded span of exactly the unit
text the approved query exception already serves it, for a source ref that it
received in the same run.** It never sees raw bytes, raw lineage,
package/part identity or native metadata. Owner decisions:
`../approvals/pr-05-agent-source-text-view.md`.

## What is served

- **The text** is the unit's `search_text` (0037, current 0039 population):
  - the retained unit text, if the unit has any;
  - otherwise its pinned package's text, but only when that package is sealed,
    matches its inventory, is complete and every part is
    `producer_normalized`.

  An identity, mixed or missing package serves nothing. That slice is
  `text_state: "unsupported"` and is never measured: no length, interval or
  byte check runs for it. The package gate is decided without reading part
  content, so unsupported text is never aggregated and its hidden length never
  reaches the charged work.
- **Labels.** `source_text_labels` are the population's own labels for the
  reply's served units: `normalized_projection:<policy>` plus declared
  exclusions and unresolved coverage. Materialization refuses a package that
  declares unresolved coverage, so a cited unit carries exclusions only.
- **Slices.** Each slice echoes its source ref and its delivering result's
  frame. `unit_characters` is the length of the served text, never package
  metadata.

## Authority

Every entry passes all of these before any interval, size or byte check. One
failure withholds the whole reply as `unavailable`.

1. **Delivered here.** The exact ref (ref, unit, content hash) is in this run's
   delivered refs. A ref first delivered by a helper reply (owner evidence
   metadata) has no delivering result to bind, so it is not served.
2. **Context.** It uses the context receipted for the ref's first delivery, or
   an explicit standalone origin whose result delivered it in this run.
   A ref delivered under a checkpoint context (0051), or a checkpoint origin,
   is refused as `unsupported_query`. It is never served as standalone.
3. **Whole closure.** The delivering result's whole closure is rechecked, once
   per distinct result per call.
4. **Binding and authority now.** The statement/unit binding comes from that
   result. Statement and bead-version authorization, and the 0037 bead and unit
   records (bead event `memory.query`; unit event `memory.query` and
   `source.read`), are evaluated now.
5. **Pinned package.** The unit's package must equal the one in the delivering
   result's closure.

Source revisiting and `source.raw.read` are never consulted.

## Limits and outcomes

Limits are UTF-8 content bytes, as for hydration:

| Bound | Over it |
|---|---|
| ≤ 16384 bytes per slice | `invalid_request/binding` |
| ≤ `max_bytes` (≤ 65536) per reply | `budget_exhausted/storage` |
| whole reply ≤ 256 KiB, paged by whole slices | a slice that cannot fit is `budget_exhausted/transport` |

Other outcomes:
- An interval past `unit_characters` is `invalid_request/binding`.
- This run's cursor presented with a changed request is
  `invalid_request/binding`. A tampered or expired cursor is
  `invalid_request/cursor`, as for other helpers.
- Exact redelivery reuses the sealed artifact under rechecked authority and is
  charged one access, its database time and its reply bytes.

## Authorization audit

- The view runs inside the executor's delivery, on connections that name that
  delivery (audit retention, 0047). Every authorization audit row it writes is
  attributed to its own delivery, and no row outside a live delivery is.
- Attributed rows are kept for the governed 30 days. Each is charged
  `query_audit_row_bytes_v1()` against the workspace's separate audit
  allowance, never the content allocation. A workspace at its allowance is
  refused before admission (`budget_exhausted/storage`), writing nothing.
- Each distinct delivering result's closure is rechecked once per call, so
  eight entries from one result write no more audit than one.

## Migration and composition

- The view's migration is the next free number after audit retention (0047),
  the pairing revision fence fix (0048), the hidden-length fix (0049), sealed
  unit query facts (0050) and automatic checkpoints (0051). Its header
  records which earlier migration each redefinition came from.
- It adds the kind to the helper kind/complete checks and to
  `admit_query_delivery_v1` (audit retention's definition, with its audit
  allowance refusal), `query_helper_delivery_v1`,
  `purge_query_helper_step_v1` and `prepare_query_helper_v1`. Each is the
  latest earlier definition with only the new kind added; tests compare them
  function by function.
- It adds `query_unit_source_text_v1`, `resolve_query_source_text_v1`,
  `bind_query_source_text_v1` and `prepare_query_source_text_v1`. The unit
  text reads packages through the hidden-length fix's
  `query_unit_package_v1`, which reads part content only for a
  producer-normalized package.
- **Hidden part count (N-2).** The view reads a unit only through
  `query_unit_package_v1` and the unit records. It has no part read of its
  own. Under the hidden-length fix alone, those still visit every part row,
  so database time could track the hidden part count. Sealed unit query facts
  come before the view and redefine both to read one sealed row (normalized
  text included), so nothing the view reaches reads a part row. Both are bound
  to the package the facts were sealed from: a pin that no longer names it
  (impossible through 0016/0017; a fixture tamper in case 23) refuses the
  whole read. A static test
  walks the stream's call graph. The runtime case reads identity units of 1
  and 64 parts, and normalized units of equal text in 1 and 64 parts, and
  shows:
  - zero part-relation blocks across the agent's whole read path. The
    control is hidden length's record builder, which the same measure sees
    reading part rows;
  - byte-identical slices;
  - equal sealed bytes and allocation.
- Automatic checkpoints (0051) precede the view. This version refuses
  checkpoint-delivered refs and checkpoint origins explicitly. Serving them
  under their checkpoint context is a separate later change.

## Tests

- `tests/unit/test_agent_source_text_view.py`: request closure, payload
  validation (including never-measured unsupported slices), page labels, and
  static migration guards.
- `tests/runtime/test_agent_source_text_runtime.py`: the database acceptance
  cases.
