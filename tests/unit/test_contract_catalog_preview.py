from __future__ import annotations

import io
import json
import os
import socket
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from memoriesql.cli import main
from memoriesql.contracts import (
    CATALOG_KINDS,
    CatalogNotFoundError,
    ContractNotFoundError,
    available_catalogs,
    contract_inventory,
    get_contract,
    iter_contracts,
    load_catalog,
)


class ContractCatalogPreviewTests(unittest.TestCase):
    def test_fixed_catalog_inventory_is_useful_and_truthful(self) -> None:
        self.assertEqual(available_catalogs(), CATALOG_KINDS)
        schemas = load_catalog("json-schema")
        self.assertEqual(schemas["kind"], "json_schema")
        self.assertEqual(schemas["status"], "available")
        schema_entries = schemas.get("entries")
        self.assertIsInstance(schema_entries, list)
        assert isinstance(schema_entries, list)
        self.assertEqual(len(schema_entries), 43)

        cursor = get_contract("memoriesql.capture.connector-cursor")
        self.assertEqual(cursor["version"], "1")
        self.assertEqual(cursor["status"], "available")
        contract = cursor.get("contract")
        self.assertIsInstance(contract, dict)
        assert isinstance(contract, dict)
        self.assertEqual(contract["type"], "object")

        unavailable = load_catalog("mcp")
        self.assertEqual(unavailable["status"], "not_implemented")
        self.assertEqual(unavailable["entries"], [])

        self.assertEqual(len(iter_contracts()), 49)
        with self.assertRaises(ContractNotFoundError):
            get_contract("memoriesql.unlisted-provider")

    def test_exact_lookups_fail_closed(self) -> None:
        with self.assertRaises(CatalogNotFoundError):
            load_catalog("unknown")
        with self.assertRaises(ContractNotFoundError):
            get_contract("memoriesql.not-a-contract")

    def test_filtering_preserves_generated_status(self) -> None:
        manifests = iter_contracts(kind="module", status="manifest_available")
        self.assertEqual(manifests, ())
        self.assertEqual(load_catalog("module")["status"], "not_implemented")
        self.assertEqual(iter_contracts(kind="module", status="available"), ())

    def test_cli_emits_machine_readable_inventory_and_exact_entry(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["contracts", "--kind", "mcp", "--json"]), 0)
        inventory = json.loads(output.getvalue())
        self.assertFalse(inventory["desktop_product_included"])
        self.assertFalse(inventory["compatibility_guarantee"])
        self.assertEqual(inventory["preview_status"], "experimental_pre_alpha")
        self.assertEqual(inventory["catalogs"][0]["status"], "not_implemented")

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "contract",
                        "memoriesql.capture.connector-cursor",
                        "--json",
                    ]
                ),
                0,
            )
        self.assertEqual(
            json.loads(output.getvalue())["id"],
            "memoriesql.capture.connector-cursor",
        )

    def test_catalog_read_has_no_external_discovery_or_network_path(self) -> None:
        def denied(*args: object, **kwargs: object) -> None:
            raise AssertionError("external access is forbidden")

        with (
            patch.object(Path, "home", denied),
            patch.object(os, "walk", denied),
            patch.object(socket, "socket", denied),
            patch.object(socket, "create_connection", denied),
        ):
            inventory = contract_inventory(kind="json_schema")
        self.assertFalse(inventory["desktop_product_included"])


if __name__ == "__main__":
    unittest.main()
