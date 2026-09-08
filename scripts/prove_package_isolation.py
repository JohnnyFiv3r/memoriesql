from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


def _denied(*args: object, **kwargs: object) -> None:
    raise AssertionError("contract preview attempted forbidden external access")


def main() -> int:
    with (
        patch.object(Path, "home", _denied),
        patch.object(os, "walk", _denied),
        patch.object(socket, "socket", _denied),
        patch.object(socket, "create_connection", _denied),
        patch.object(subprocess, "Popen", _denied),
    ):
        from memoriesql.contracts import contract_inventory, get_contract

        inventory = contract_inventory(kind="json_schema")
        contract = get_contract("memoriesql.capture.connector-cursor")

    forbidden_imports = sorted(
        name
        for name in sys.modules
        if name.split(".", 1)[0]
        in {"psycopg", "pydantic", "pydantic_ai", "sqlalchemy"}
    )
    if forbidden_imports:
        raise AssertionError(
            "developer preview imported runtime dependencies: "
            + ", ".join(forbidden_imports)
        )
    print(
        json.dumps(
            {
                "desktop_product_included": inventory["desktop_product_included"],
                "contract_id": contract["id"],
                "external_access": False,
                "runtime_dependency_imports": forbidden_imports,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
