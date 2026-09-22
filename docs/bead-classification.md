# Attributable bead classification v1

Published in0.0.8 and retained in 0.0.9 and the prepared 0.0.10 candidate, this merged slice builds on authored
local mentions at schema 22.
Schema 23/task `memory.semantic.author-complete-unit` revision 5 pins explicitly
selected registered bead-type definitions at activation. It uses the existing
source-revisiting author, one sequential typed classification leaf, queue, run
ledger, accounting, trusted dispatch attestor and atomic canonical apply. It
introduces no provider transport, semantic store, inference on read or release.

The primary returns substantive statements, mentions, short renders and exact
supporting selections. The classifier receives that whole proposal, pinned
versioned definitions and exact authorized selected source content. Packet bounds
are 32 definitions, eight selections and 262,144 canonical UTF-8 bytes; overflow
fails without truncation. The primary still must receive every mandatory target
interval and controls its rereads. Optional/raw specialist support must have been
exposed to the primary. Classifier receipt version 3 never populates primary
exposure rows. Every actual request uses the existing intent/usage path.

`ClassificationDecision` v1 records selected/no_fit/insufficient_evidence/ambiguous,
an optional selected registered type, explicit consistent/conflicting/uncertain
proposal assessment, an optional rationale and optional diagnostic label/alignment distributions and
confidence. A decision-only endpoint need not generate prose: absent rationale
stays absent. Available distributions include abstention choices; no score gates
acceptance.
Acceptance requires the classifier's authored consistency assessment and exact
agreement with the primary's proposed type/revision. Conflicts and abstentions
remain unaccepted; there is no voting, relabeling or synthetic success. Further
authored reconsideration requires new work; this bounded revision does not loop
or add a generative rubber-stamp call. Schema checks do not prove semantic quality.

`ClassifiedExecutionOutput` v1 adds one `classification` contribution to the
revision-4 mentions bundle. The aggregate accepted JSON limit remains 12,000
UTF-8 bytes including mentions and diagnostics. `ApplyClassifiedAuthorship` v6
requires the two exact runs. `ActivateClassifiedAuthorship` v4 requires schema 23;
activation binds existing vocabulary key/revision pairs without inventing entries.

## Stored shape and Q handoff

* `bead_versions.classification_contribution` stores the exact accepted v1
  contribution; existing type and type-revision foreign keys remain authoritative.
  The accepted bead version and its own semantic-task receipt own this value.
* `request_id` is the stable opaque contribution identity. It links existing
  `model_provider_request_intents`, `semantic_task_runs` and `model_usage_events`:
  model/profile/provider/credential identity, policy/pricing/quality revisions,
  task/attempt/lease and actual or unknown usage remain in those existing records.
* `author_run_ref`, `model_run_ref`, packet/proposal/vocabulary SHA-256 pins and
  exact evidence selection/hash pairs establish attribution. The proposal hash
  covers substantive statements, mentions, type and render, not a summary alone.
* `complete_input_executions.classification_vocabulary` preserves exact definitions
  supplied to this attempt. Original materialization/task/package pins still own
  source lineage. Evidence selections are normalized character or declared raw
  byte intervals, not invented statement links. Diagnostics are model judgments,
  not source evidence, author confidence, truth or navigation weights.
* Existing trusted dispatch receipts with `delivery_version=3` retain returned
  assessment provenance, including an abstention, separately from acceptance.
  A receipt alone does not imply an accepted rich bead. Failed/pending attempts
  retain evidence/thin work; missing usage is unknown.

Q owns bounded currently authorized stored-result reads. No new read API is
implemented here. Check the accepted version's own task receipt before presenting
capability. Legacy NULL is unsupported/absent, not an empty classification. A
revision-5 accepted result requires one contribution; there is no valid empty
classification collection. Mentions may be explicitly empty under revisions 4/5
only after a complete authorized read. Missing, pending, failed and withheld
states must not become empty. Local unresolved/ambiguous mentions are distinct
from governed entity resolution: choose the latest decision before authorization,
never fall back to an older visible resolution or turn hidden into unresolved.
Authorize every selected source and provenance record before returning it; do not
leak protected classification, rationale or existence through partial aggregates.

P owns migration 0023, its activation/apply/attestation changes and classification
fixtures. Q reserves the following migration for authorized reads. Shared registry,
catalog, migration/runtime/provenance inventories must be reconciled explicitly.
Q public work may stack on the verified P commit. Desktop consumption waits for
review, merge and a separately authorized public release, alongside admission PR
#20. Historical SQL 0001–0022 and records remain unchanged. Migration rollback is
forward-only: restore the pre-upgrade backup; never downgrade accepted semantics.

Fictional fixtures: `tests/runtime/test_bead_classification.py` covers accepted,
authored-empty mentions, typed abstentions, disagreement, invalid label, failure,
revocation, missing exposure, forged contribution, rollback, replay and immutable
accepted values. The inherited `setup_revisiting(..., activate=False)` produces
retained/pending thin work. `test_local_entity_mentions.py` provides revision-4
accepted results with absent classification; earlier complete-input fixtures
provide legacy mentions capability. No fixture is provider quality proof.
