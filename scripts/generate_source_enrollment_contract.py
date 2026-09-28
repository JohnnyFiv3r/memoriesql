"""Generate the provider-neutral exact-source authority contract record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application.source_enrollment import (
    EnrollExactSource,
    ExactSourceEnrollment,
    ExactSourceGrant,
    ExactSourceRevocation,
    GrantExactSource,
    RevokeExactSource,
)

PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/records/memoriesql-source-enrollment-v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    record = dict(
        id="memoriesql.source-enrollment.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Authenticated, exact provider-neutral source enrollment, scoped grant and revocation receipts; no source bytes or provider adapter.",
        contract=dict(
            schemas={
                model.__name__: model.model_json_schema()
                for model in (
                    EnrollExactSource,
                    ExactSourceEnrollment,
                    GrantExactSource,
                    ExactSourceGrant,
                    RevokeExactSource,
                    ExactSourceRevocation,
                )
            }
        ),
    )
    data = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != data:
            raise SystemExit("source enrollment contract drift")
    else:
        PATH.write_text(data)


if __name__ == "__main__":
    main()
