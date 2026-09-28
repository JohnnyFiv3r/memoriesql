"""Generate the provider-neutral local client pairing contract record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application.local_client_pairing import (
    LocalClientPairing,
    LocalClientRevocation,
    PairLocalClient,
    RevokeLocalClient,
)

PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/records/memoriesql-local-client-pairing-v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    record = dict(
        id="memoriesql.local-client-pairing.v1",
        version="1",
        kind="json_schema",
        status="available",
        summary="Authenticated pairing and terminal revocation of one local agent client with explicit capabilities, owned scopes and expiry; the secret never enters a receipt.",
        contract=dict(
            schemas={
                model.__name__: model.model_json_schema()
                for model in (
                    PairLocalClient,
                    LocalClientPairing,
                    RevokeLocalClient,
                    LocalClientRevocation,
                )
            }
        ),
    )
    data = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != data:
            raise SystemExit("local client pairing contract drift")
    else:
        PATH.write_text(data)


if __name__ == "__main__":
    main()
