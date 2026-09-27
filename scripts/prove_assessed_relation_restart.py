"""Three installed processes prove a lost governance response and durable replay.

The handoff contains fictional requests and identities only. Recovery imports no
fixture, does no model work, and runs with checkout/provider access denied.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main() -> None:
    mode = sys.argv[1]
    path = Path("fictional-assessed-relation-session.json")
    if mode == "prepare":
        sys.path.insert(0, str(Path.cwd()))
        if TYPE_CHECKING:
            from tests.runtime.test_assessed_relation_lifecycle import AssessedLifecycle
        else:
            from test_assessed_relation_lifecycle import AssessedLifecycle
        fixture = AssessedLifecycle()
        fixture.setUp()
        try:
            bead, _, relation = fixture.assertion()
            command = fixture.lifecycle_command(
                bead, relation, "retract", "lost-response"
            )
            receipt = fixture.governance.record_event(command)
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
                        task_count=fixture.row(
                            "SELECT count(*) FROM memoriesql.semantic_tasks"
                        )[0],
                    )
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
        from memoriesql.infrastructure.postgres.assessed_relation_lifecycle import (
            PostgresAssessedRelationLifecycle,
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
    elif mode == "cleanup":
        context = json.loads(path.read_text())
        with psycopg.connect(os.environ["N1_TEST_DATABASE_URL"], autocommit=True) as db:
            db.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(context["database"])
                )
            )
        path.unlink()
        print("fictional replay database removed")
    else:
        raise SystemExit("mode must be prepare, recover or cleanup")


if __name__ == "__main__":
    main()
