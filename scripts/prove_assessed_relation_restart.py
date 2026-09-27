"""Three installed processes prove a lost governance response and durable replay.

The handoff contains fictional requests and identities only. Recovery imports no
fixture, does no model work, and runs with checkout/provider access denied.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main() -> None:
    mode = sys.argv[1]
    path = Path("fictional-assessed-relation-session.json")
    if mode == "prepare":
        sys.path.insert(0, str(Path.cwd()))
        if TYPE_CHECKING:
            from tests.runtime.query_authority_fixture import provision_fictional_reader
            from tests.runtime.test_assessed_relation_lifecycle import AssessedLifecycle
            from tests.runtime.test_postgres_runtime import migrate
        else:
            from query_authority_fixture import provision_fictional_reader
            from test_assessed_relation_lifecycle import AssessedLifecycle
            from test_postgres_runtime import migrate
        from memoriesql.infrastructure.postgres.relation_sql_population import (
            prepare_relation_population,
        )
        from memoriesql.infrastructure.postgres.restricted_query import (
            PostgresRestrictedQuery,
        )
        from memoriesql.infrastructure.postgres.result_preparation import (
            PostgresResultPreparation,
        )

        fixture = AssessedLifecycle()
        fixture.setUp()
        try:
            bead, _, relation = fixture.assertion()
            migrate(fixture.db, expected_current_version=30, target_version=33)
            reader_name = "pr05_restart_" + uuid4().hex
            profile = provision_fictional_reader(fixture.db, reader_name)
            run_ref, step_key = uuid4(), uuid4()
            store = PostgresResultPreparation(
                fixture.db,
                credential_sha256=fixture.secret_hash,
                workspace_id=fixture.workspace,
            )
            ownership = store.reserve(
                run_ref=run_ref,
                step_key=step_key,
                request_fingerprint="b" * 64,
                reservation_bytes=64 * 1024 * 1024,
            )
            executor = PostgresRestrictedQuery(
                reader_factory=lambda: psycopg.connect(
                    make_conninfo(
                        os.environ["N1_TEST_DATABASE_URL"],
                        dbname=fixture.database,
                        user=reader_name,
                        password="fictional-only",
                    ),
                    autocommit=True,
                ),
                control_factory=fixture.connection,
                authority_profile=profile,
                credential_sha256=fixture.secret_hash,
                workspace_id=fixture.workspace,
                policy_hash="5" * 64,
            )
            command = fixture.lifecycle_command(
                bead, relation, "retract", "lost-response"
            )
            receipt = fixture.governance.record_event(command)
            started = time.monotonic()
            with prepare_relation_population(
                fixture.db,
                credential_sha256=fixture.secret_hash,
                workspace_id=fixture.workspace,
                known_at=None,
                byte_budget=64 * 1024 * 1024,
            ) as population:
                projection_witness = dict(
                    known_at=population.known_at.isoformat(),
                    dependency_manifest=population.dependency_manifest.decode(),
                    assertions=json.dumps(
                        population.rows["memory_v1.assessed_relations"], default=str
                    ),
                    preparation_bytes=population.preparation_bytes,
                    dependency_bytes=len(population.dependency_records),
                    row_counts={k: len(v) for k, v in population.rows.items()},
                    elapsed_seconds=time.monotonic() - started,
                )
                native = executor.execute(
                    fixture.db,
                    population,
                    ownership,
                    "SELECT relation_id,state FROM memory_v1.assessed_relations",
                )
                assert (
                    native.outcome == "complete"
                    and native.settlement_state == "settled"
                ), native
                assert native.rows == ((relation, "retracted"),), native
                native_witness = dict(
                    run_ref=str(run_ref),
                    step_key=str(step_key),
                    operation_ref=str(ownership.operation_ref),
                    ownership_ref=str(ownership.ownership_ref),
                    invocation_ref=str(native.invocation_ref),
                    profile=asdict(profile),
                )
            # The transport result is deliberately unavailable to the recovering
            # caller. An independent proof witness retains it for byte comparison.
            path.write_text(
                json.dumps(
                    dict(
                        database=fixture.database,
                        credential_sha256=fixture.secret_hash,
                        workspace_id=str(fixture.workspace),
                        bead_id=str(bead),
                        command=command.model_dump(mode="json"),
                        witness=receipt.model_dump(mode="json"),
                        projection_witness=projection_witness,
                        native_witness=native_witness,
                        task_count=fixture.row(
                            "SELECT count(*) FROM memoriesql.semantic_tasks"
                        )[0],
                    ),
                    default=lambda value: sorted(value),
                )
            )
        except BaseException:
            fixture.cleanup()
            raise
        fixture.db.close()
        print("setup exited after committed withdrawal; response deliberately lost")
    elif mode == "recover":
        sys.path.insert(0, str(Path.cwd()))
        if TYPE_CHECKING:
            from scripts.run_installed_acceptance import install_guard
        else:
            from run_installed_acceptance import install_guard
        install_guard()
        from memoriesql.application.assessed_relation_lifecycle import (
            AssessedRelationEventReceipt,
            BeadRelationsInspectionV3,
            InspectBeadRelationsV3,
            RecordAssessedRelationEvent,
        )
        from memoriesql.infrastructure.postgres.agent_sql_authority import (
            QualifiedView,
            QueryAuthorityProfile,
        )
        from memoriesql.infrastructure.postgres.assessed_relation_lifecycle import (
            PostgresAssessedRelationLifecycle,
        )
        from memoriesql.infrastructure.postgres.relation_sql_population import (
            prepare_relation_population,
        )
        from memoriesql.infrastructure.postgres.restricted_query import (
            PostgresRestrictedQuery,
        )
        from memoriesql.infrastructure.postgres.result_preparation import (
            PostgresResultPreparation,
        )

        context = json.loads(path.read_text())
        with psycopg.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"], dbname=context["database"]
            ),
            autocommit=True,
        ) as db:
            port = PostgresAssessedRelationLifecycle(
                db,
                credential_sha256=context["credential_sha256"],
                workspace_id=UUID(context["workspace_id"]),
            )
            receipt = port.record_event(
                RecordAssessedRelationEvent.model_validate(context["command"])
            )
            assert isinstance(receipt, AssessedRelationEventReceipt), receipt
            assert receipt.replayed
            assert receipt.model_dump(mode="json") == context["witness"] | dict(
                replayed=True
            )
            frame = port.inspect_relations(
                InspectBeadRelationsV3(
                    contract_version=3, bead_id=UUID(context["bead_id"]), known_at=None
                )
            )
            assert isinstance(frame, BeadRelationsInspectionV3), frame
            assert frame.relations[0].state == "retracted"
            assert not frame.relations[0].support_eligible
            projection = context["projection_witness"]
            # A fresh canonical historical projection, NOT saved-result reuse or
            # a checkpoint restore. The original governance replay stays durable.
            with prepare_relation_population(
                db,
                credential_sha256=context["credential_sha256"],
                workspace_id=UUID(context["workspace_id"]),
                known_at=datetime.fromisoformat(projection["known_at"]),
                byte_budget=64 * 1024 * 1024,
            ) as population:
                assert (
                    population.dependency_manifest.decode()
                    == projection["dependency_manifest"]
                )
                assert (
                    json.dumps(
                        population.rows["memory_v1.assessed_relations"], default=str
                    )
                    == projection["assertions"]
                )
                native = context["native_witness"]
                raw = native["profile"]
                profile = QueryAuthorityProfile(
                    reader=raw["reader"],
                    database_oid=raw["database_oid"],
                    server_version=raw["server_version"],
                    schema_oids=frozenset(raw["schema_oids"]),
                    views=tuple(
                        QualifiedView(
                            v["oid"],
                            v["owner"],
                            v["definition_sha256"],
                            tuple(v["backing_tables"]),
                        )
                        for v in raw["views"]
                    ),
                    procedure_hashes={
                        int(k): v for k, v in raw["procedure_hashes"].items()
                    },
                    table_hashes={int(k): v for k, v in raw["table_hashes"].items()},
                    default_creator_oids=frozenset(raw["default_creator_oids"]),
                )
            store = PostgresResultPreparation(
                db,
                credential_sha256=context["credential_sha256"],
                workspace_id=UUID(context["workspace_id"]),
            )
            ownership = store.reserve(
                run_ref=UUID(native["run_ref"]),
                step_key=UUID(native["step_key"]),
                request_fingerprint="b" * 64,
                reservation_bytes=64 * 1024 * 1024,
            )
            assert (
                ownership.replayed
                and str(ownership.operation_ref) == native["operation_ref"]
                and str(ownership.ownership_ref) == native["ownership_ref"]
            )

            def no_reader() -> psycopg.Connection[object]:
                raise AssertionError("restart cannot dispatch original SQL")

            def control() -> psycopg.Connection[object]:
                return psycopg.connect(
                    make_conninfo(
                        os.environ["N1_TEST_DATABASE_URL"], dbname=context["database"]
                    ),
                    autocommit=True,
                )

            executor = PostgresRestrictedQuery(
                reader_factory=no_reader,
                control_factory=control,
                authority_profile=profile,
                credential_sha256=context["credential_sha256"],
                workspace_id=UUID(context["workspace_id"]),
                policy_hash="5" * 64,
            )
            settled = executor.recover(ownership)
            assert (
                settled is not None
                and settled.state == "settled"
                and settled.outcome == "complete"
                and str(settled.invocation_ref) == native["invocation_ref"]
            )
            replay = executor.execute(
                db,
                population,
                ownership,
                "SELECT relation_id,state FROM memory_v1.assessed_relations",
            )
            assert replay.outcome == "settlement_pending" and not replay.rows
            assert db.execute(
                "SELECT count(*) FROM memoriesql_query.invocations"
            ).fetchone() == (1,)
            assert db.execute(
                "SELECT count(*) FROM memoriesql_query.population_rows"
            ).fetchone() == (0,)
            for query, expected in (
                ("SELECT count(*) FROM memoriesql.assessed_relation_events", 1),
                (
                    "SELECT count(*) FROM memoriesql.semantic_tasks",
                    context["task_count"],
                ),
                (
                    "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'",
                    0,
                ),
            ):
                row = db.execute(query).fetchone()
                assert row is not None and row[0] == expected, (row, expected)
        print(
            "fresh installed process recovered the original receipt; one event, zero new tasks"
        )
        print(
            "fresh canonical historical SQL population matches; no saved-result reuse claim"
        )
        print(
            "original restricted invocation journal recovered; no SELECT rerun or available-result claim"
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in projection.items()
                    if k not in {"dependency_manifest", "assertions"}
                },
                sort_keys=True,
            )
        )
    elif mode == "cleanup":
        context = json.loads(path.read_text())
        with psycopg.connect(os.environ["N1_TEST_DATABASE_URL"], autocommit=True) as db:
            db.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(context["database"])
                )
            )
            db.execute(
                sql.SQL("DROP ROLE {}").format(
                    sql.Identifier(context["native_witness"]["profile"]["reader"])
                )
            )
        path.unlink()
        print("fictional replay database removed")
    else:
        raise SystemExit("mode must be prepare, recover or cleanup")


if __name__ == "__main__":
    main()
