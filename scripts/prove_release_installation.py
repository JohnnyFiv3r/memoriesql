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
        from memoriesql.application.bead_classification import (
            ActivateClassifiedAuthorship,
        )
        from memoriesql.application.local_entity_mentions import (
            ActivateMentionAuthorship,
        )
        from memoriesql.application.source_stable_identity import (
            MaterializeSourceStableUnit,
        )
        from memoriesql.application.stored_bead_inspection import InspectStoredBead
        from memoriesql.cli import main as cli
        from memoriesql.contracts import get_contract, iter_contracts
        from memoriesql.infrastructure.models.pydanticai_executor import (
            RealModelAdmission,
        )
        from memoriesql.infrastructure.postgres.logical_unit_materialization import (
            PostgresLogicalUnitMaterialization,
        )
        from memoriesql.infrastructure.postgres.migration_runner import (
            discover_migrations,
        )
        from memoriesql.infrastructure.postgres.stored_bead_inspection import (
            PostgresStoredBeadInspection,
        )

        installed = distribution("memoriesql")
        assert installed.version == __version__ == "0.0.11"
        owned = {
            Path(str(installed.locate_file(p))).resolve() for p in installed.files or ()
        }
        migrations = discover_migrations()
        assert len(migrations) == 29
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
        assert len(tuple(iter_contracts())) == 63
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
        for identifier in (
            "memoriesql.local-entity-mentions.v1",
            "memoriesql.authored-relations.v1",
            "memoriesql.relation-profile.v1",
            "memoriesql.bead-classification.v1",
            "memoriesql.stored-bead-inspection.v1",
        ):
            assert get_contract(identifier)["id"] == identifier
        assert (
            ActivateMentionAuthorship.model_fields["expected_schema_version"].default
            == 22
        )
        assert (
            ActivateClassifiedAuthorship.model_fields["expected_schema_version"].default
            == 23
        )
        assert "bead_id" in InspectStoredBead.model_fields
        assert callable(PostgresStoredBeadInspection.inspect)
        assert callable(PostgresStoredBeadInspection.read)
        assert RealModelAdmission.__dataclass_fields__["model"]
        output = io.StringIO()
        with redirect_stdout(output):
            try:
                cli(["--version"])
            except SystemExit as result:
                assert result.code == 0
        assert output.getvalue().strip() == "0.0.11"
    print(
        json.dumps(
            {
                "version": installed.version,
                "migrations": len(migrations),
                "contracts": len(tuple(iter_contracts())),
                "source_stable_materialization": True,
                "admission_mentions_classification_inspection": True,
                "database_or_provider_access": False,
            }
        )
    )


if __name__ == "__main__":
    main()
