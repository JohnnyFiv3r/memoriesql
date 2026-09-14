"""Generate the explicitly registered public fold recovery contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import fold_recovery as fr

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-transcript-fold-recovery-v1.json"


def record() -> dict[str, object]:
    models = (
        fr.DiscoverFoldOutcomes,
        fr.InspectFoldOutcome,
        fr.PageFoldLineage,
        fr.ReadFoldEvidence,
        fr.FoldDiscoveryPage,
        fr.FoldOutcomeDetail,
        fr.FoldLineagePage,
        fr.FoldEvidencePage,
    )
    return dict(
        id="memoriesql.transcript-fold.recovery.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Bounded current-authority recovery of acknowledged fold facts and exact retained derivation; no qualification or semantic writes.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            limits=dict(
                discovery_outcomes=fr.DISCOVERY_LIMIT,
                lineage_ranges=fr.LINEAGE_LIMIT,
                read_bytes=fr.READ_BYTES,
                request_json_bytes=fr.REQUEST_BYTES,
                response_json_bytes=fr.RESPONSE_BYTES,
                operation_timeout_ms=fr.OPERATION_TIMEOUT_MS,
                lock_timeout_ms=fr.LOCK_TIMEOUT_MS,
            ),
            authorization="Current source.raw.read resource, capability, role and delegation checks before and after the existing authority fence; service access requires explicit policy and grant.",
            continuation="Source-scoped immutable receipt watermark and last outcome key; continue within the watermark, then fresh discovery after resume_after to include later committed receipts. Positions are recovery order, not source chronology.",
            evidence="Hex encodes exact byte pages, including Unicode boundaries. Exact envelopes are normalized stored facts; raw lineage is distinct. Neither implies source completeness, qualification or author exposure.",
            creates_semantic_artifacts=False,
        ),
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("fold recovery contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
