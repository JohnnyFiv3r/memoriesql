# PR-05 preview amendments (owner-approved, Decision 1)

The owner-approved amendment record is `pr-05-preview-amendments-v1.json`. It
amends the approved record `pr-05-settings-approval-candidate-v2.json` for a
labelled preview. It does not rewrite that record, the approved packet, the
lifecycle dependency or any custody bytes.

The exact interface was reverified from Git blob bytes before recording:

| Pin | Value |
| --- | --- |
| Commit | `b3865f307b50bd94681b387ad0f3b16fbb20213a` |
| Packet SHA-256 (`docs/agent-sql-results-v1.md`) | `78bbc04eaeffd0fd47b0576e270f25eaa30e07a90b42e53d27e64308dfd8269e` |
| Lifecycle dependency SHA-256 (`docs/relation-assessment.md`) | `69a0d985466c6610a3a2abc73bcef9242749ff9535cb709e32867c7e06414d2a` |
| Approved record SHA-256 | `bcc4398a2f3bba618ae9eb85ee331fb0bf94ac9108bd42f161e5cc07d3a7a478` |
| Custody ea-3 SHA-256 (12,916 bytes) | `12e802be3664a2c971ae144f692e993e43344e9d68dfff569cfc16f77547e60b` |

## Source

The PR-05 coordinator relayed the owner's confirmation. The owner confirmed on
or before 2026-09-28T21:26:24.818Z (16:26 CDT), the time the relay reached the
implementer. The coordinator withdrew an earlier unchecked time estimate from the
relay; only this receipt bound is recorded. The same coordinator's earlier
recommendation, received at 21:21:35Z, carried the reviewer's qualifications. The
record quotes the confirmed texts verbatim.

## Amendments

Item numbers are Decision 1's.

- **Item 1: measured small-workspace preview.** The preview range is defined
  only by the measured small-workspace qualification, not by extrapolation. It
  covers:
  - query, paging, reuse and source inspection;
  - sizing by the prepared population;
  - proof that exhaustion leaves no partial result or abandoned work.
- **Item 2: temporary logical-accounting amendment.** It comes with a basic
  before/after database-growth measurement in the same run. Logical allocation
  accounting is not a physical disk guarantee, and 512 MiB is never described as
  disk usage.
- **Item 4: erasure explicitly unavailable,** in the reviewer's exact wording:

  > Governed erasure is unavailable in this preview. Revocation prevents
  > subsequent authorized disclosure; it does not delete retained data or recall
  > previously delivered copies. Regrant may restore access while the result
  > remains valid. Expired derived-result content is removed through qualified
  > expiry cleanup. Canonical source deletion and immediate erasure are not
  > provided by this release.

Item 3 of Decision 1 is **not** an amendment. Expired-result deletion must be
finished with its listed qualification:
- content and sensitive copies;
- interrupted cleanup and restart recovery;
- accounting reclamation;
- visible failure status and recovery;
- a stated deadline mechanism.

## What stays unchanged

Every wire shape, outcome, safe error code and numeric policy of the approved
packet stays as approved.

This record implies no security waiver, no full-acceptance claim, no release
authorization and no owner-credential fallback for agents.

Two gates stay in force:
- **Agent-identity access**, from the paired-agent zero-rows repro. It is
  unproven until its fix lands with a paired-agent regression test.
- **The Desktop lane's trust boundary.**

No unseen questions, gold, hints or access paths were accessed or received.
