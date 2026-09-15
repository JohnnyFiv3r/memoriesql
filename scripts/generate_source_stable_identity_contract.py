"""Generate only the explicit source-stable materialization record."""

import argparse
import json
from pathlib import Path

from memoriesql.application.source_stable_identity import (
    MaterializeSourceStableUnit,
    SourceStableMaterializationReceipt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = (
        Path(__file__).resolve().parents[1]
        / "contracts/records/memoriesql-source-stable-identity-v1.json"
    )
    record = {
        "id": "memoriesql.source-stable-identity.v1",
        "version": "1",
        "kind": "json_schema",
        "status": "available",
        "summary": "Explicit qualified source-stable materialization preserving exact revision-bearing evidence and first bindings.",
        "contract": {
            "schemas": {
                m.__name__: m.model_json_schema()
                for m in (
                    MaterializeSourceStableUnit,
                    SourceStableMaterializationReceipt,
                )
            },
            "identity_scope": [
                "tenant",
                "source_object",
                "approved_namespace",
                "occurrence_key",
            ],
            "event_scope": [
                "tenant",
                "source_object",
                "approved_namespace",
                "event_key",
            ],
            "legacy_transition": "populated_source_unsupported",
            "content_consistency": "ordered_logical_components_exact_utf8",
            "source_completeness_independently_proven": False,
        },
    }
    expected = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.check:
        if path.read_text() != expected:
            raise SystemExit("source-stable identity schema drift")
    else:
        path.write_text(expected)


if __name__ == "__main__":
    main()
