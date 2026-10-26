"""Generate the Core Memory engine record intake contract record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application.engine_intake import (
    AcceptEngineRecords,
    EngineRecordPage,
    EngineRecordResults,
    EngineVocabulary,
    ReadEngineRecords,
)

PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/records/memoriesql-engine-intake-v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    record = dict(
        id="memoriesql.engine-intake.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Non-authoring intake of Core Memory engine chunk and type-decision records into enrolled engine scopes, with one outcome per record, an authorized reader and the governed bead types as the engine vocabulary.",
        contract=dict(
            schemas={
                model.__name__: model.model_json_schema()
                for model in (
                    AcceptEngineRecords,
                    EngineRecordResults,
                    ReadEngineRecords,
                    EngineRecordPage,
                    EngineVocabulary,
                )
            }
        ),
    )
    data = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != data:
            raise SystemExit("engine intake contract drift")
    else:
        PATH.write_text(data)


if __name__ == "__main__":
    main()
