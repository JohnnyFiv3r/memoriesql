"""Verify a fresh installed release without migration, activation or network access."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import socket
from contextlib import redirect_stdout
from importlib.metadata import distribution
from pathlib import Path
from unittest.mock import patch


def denied(*args: object, **kwargs: object) -> None:
    raise AssertionError("release smoke must not access a database or provider")


def main() -> None:
    with (
        patch.object(socket, "socket", denied),
        patch.object(socket, "create_connection", denied),
    ):
        from memoriesql import __version__
        from memoriesql.application.source_stable_identity import (
            MaterializeSourceStableUnit,
        )
        from memoriesql.cli import main as cli
        from memoriesql.contracts import get_contract, iter_contracts
        from memoriesql.infrastructure.postgres.logical_unit_materialization import (
            PostgresLogicalUnitMaterialization,
        )
        from memoriesql.infrastructure.postgres.migration_runner import (
            discover_migrations,
        )

        installed = distribution("memoriesql")
        assert installed.version == __version__ == "0.0.7"
        owned = {
            Path(str(installed.locate_file(p))).resolve() for p in installed.files or ()
        }
        migrations = discover_migrations()
        assert len(migrations) == 24
        for migration in migrations:
            assert Path(str(migration.path)).resolve() in owned
            assert (
                hashlib.sha256(migration.path.read_bytes()).hexdigest()
                == migration.sha256
            )
        for record in installed.files or ():
            if record.hash and str(record).startswith("memoriesql/"):
                payload = Path(str(installed.locate_file(record))).read_bytes()
                assert (
                    base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                    .decode()
                    .rstrip("=")
                    == record.hash.value
                )
        assert len(tuple(iter_contracts())) == 59
        assert (
            get_contract("memoriesql.source-stable-identity.v1")["id"]
            == "memoriesql.source-stable-identity.v1"
        )
        assert MaterializeSourceStableUnit.model_fields["contract_version"].default == 2
        assert (
            MaterializeSourceStableUnit.model_fields["expected_schema_version"].default
            == 21
        )
        assert callable(PostgresLogicalUnitMaterialization.materialize_source_stable)
        output = io.StringIO()
        with redirect_stdout(output):
            try:
                cli(["--version"])
            except SystemExit as result:
                assert result.code == 0
        assert output.getvalue().strip() == "0.0.7"
    print(
        json.dumps(
            {
                "version": installed.version,
                "migrations": len(migrations),
                "contracts": len(tuple(iter_contracts())),
                "source_stable_materialization": True,
                "database_or_provider_access": False,
            }
        )
    )


if __name__ == "__main__":
    main()
