"""Read-only command line for the memoriesQL contract-catalog preview."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from memoriesql import __version__
from memoriesql.contracts import (
    CATALOG_KINDS,
    ContractNotFoundError,
    contract_inventory,
    get_contract,
)


def _write_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memoriesql",
        description="Inspect bundled memoriesQL public contract catalogs.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    contracts = commands.add_parser(
        "contracts", help="List bundled public contract catalogs and entries."
    )
    contracts.add_argument(
        "--kind",
        choices=CATALOG_KINDS,
        help="Limit output to one catalog kind.",
    )
    contracts.add_argument(
        "--status", help="Limit entries to one exact generated status."
    )
    contracts.add_argument(
        "--json", action="store_true", help="Emit stable machine-readable JSON."
    )

    contract = commands.add_parser(
        "contract", help="Show one exact contract entry by identifier."
    )
    contract.add_argument("identifier")
    contract.add_argument(
        "--json", action="store_true", help="Emit stable machine-readable JSON."
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    if args.command == "contracts":
        inventory = contract_inventory(kind=args.kind, status=args.status)
        if args.json:
            _write_json(inventory)
        else:
            print("memoriesQL experimental contract-catalog preview")
            catalogs = inventory.get("catalogs")
            if not isinstance(catalogs, list):
                raise RuntimeError("generated catalog inventory is malformed")
            for catalog in catalogs:
                if not isinstance(catalog, dict):
                    continue
                entries = catalog.get("entries")
                entry_count = len(entries) if isinstance(entries, list) else 0
                print(
                    f"{catalog.get('kind')}: {catalog.get('status')} "
                    f"({entry_count} entries)"
                )
        return 0

    try:
        entry = get_contract(args.identifier)
    except ContractNotFoundError as error:
        _parser().error(str(error))
    if args.json:
        _write_json(entry)
    else:
        print(f"{entry['id']} {entry['version']} [{entry['status']}]")
        print(entry["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
