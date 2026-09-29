"""Generate the personal-local owner initialization contract record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application.personal_local_initialization import (
    InitializePersonalLocal,
    PersonalLocalInitialization,
)

PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/records/memoriesql-personal-local-initialization-v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    record = dict(
        id="memoriesql.personal-local-initialization.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="One-time, replayable initialization of the single personal-local owner, workspace and expiring owner credential; the secret never enters a receipt.",
        contract=dict(
            schemas={
                model.__name__: model.model_json_schema()
                for model in (InitializePersonalLocal, PersonalLocalInitialization)
            }
        ),
    )
    data = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != data:
            raise SystemExit("personal-local initialization contract drift")
    else:
        PATH.write_text(data)


if __name__ == "__main__":
    main()
