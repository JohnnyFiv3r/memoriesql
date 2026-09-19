"""Generate the explicit Q stored-result inspection record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application.stored_bead_inspection import (
    InspectStoredBead,
    ReadStoredBeadEvidence,
    StoredBeadEvidence,
    StoredBeadInspection,
)

PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/records/memoriesql-stored-bead-inspection-v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    record = dict(
        id="memoriesql.stored-bead-inspection.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Bounded authorized stored bead meaning and contribution provenance; no inference.",
        contract=dict(
            schemas={
                m.__name__: m.model_json_schema()
                for m in (
                    InspectStoredBead,
                    StoredBeadInspection,
                    ReadStoredBeadEvidence,
                    StoredBeadEvidence,
                )
            }
        ),
    )
    data = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != data:
            raise SystemExit("stored bead inspection contract drift")
    else:
        PATH.write_text(data)


if __name__ == "__main__":
    main()
