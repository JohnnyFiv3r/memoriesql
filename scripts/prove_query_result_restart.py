"""Separate installed processes prove query redelivery after executor death.

`prepare` commits a fictional query result and exits abruptly before any
disclosure. `recover`, a fresh process, resends the identical request bytes: the
dead owner's delivery is settled at its full reservation and the committed
result is disclosed without dispatching the SELECT again. No owner data, model
call or production database is involved.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

SESSION = Path("fictional-query-restart-session.json")


def main() -> None:
    mode = sys.argv[1]
    sys.path.insert(0, str(Path.cwd()))
    if mode == "prepare":
        if TYPE_CHECKING:
            from tests.runtime.test_agent_sql_results import (
                READER_PASSWORD,
                AgentSqlResults,
            )
        else:
            from test_agent_sql_results import READER_PASSWORD, AgentSqlResults
        from memoriesql.infrastructure.postgres.agent_sql_results import (
            PostgresAgentSqlResults,
        )

        case = AgentSqlResults(
            methodName="test_query_page_cursor_reuse_and_redelivery_without_rerun"
        )
        case.setUp()
        case.fixture.assertion()
        run = case.start()
        request, _ = case.request_only(
            run, "SELECT bead_id,title FROM memory_v1.observations ORDER BY bead_id"
        )
        SESSION.write_text(
            json.dumps(
                {
                    "database": case.db.info.dbname,
                    "reader": case.reader,
                    "reader_password": READER_PASSWORD,
                    "credential_sha256": case.fixture.secret_hash,
                    "workspace_id": str(case.fixture.workspace),
                    "request": request,
                }
            )
        )

        def die(*args: Any, **kwargs: Any) -> bytes:
            # Committed, not yet disclosed: the whole process ends abruptly,
            # taking its owner session and every connection with it.
            print("prepare committed a result and exited before disclosure", flush=True)
            os._exit(0)

        PostgresAgentSqlResults._disclose = die  # type: ignore[method-assign]
        case.service().handle(json.dumps(request).encode())
        raise SystemExit("prepare did not reach the committed-result boundary")
    if mode == "recover":
        if TYPE_CHECKING:
            from scripts.run_installed_acceptance import install_guard
        else:
            from run_installed_acceptance import install_guard
        install_guard()
        from memoriesql.infrastructure.postgres.agent_sql_results import (
            PostgresAgentSqlResults,
        )
        from memoriesql.infrastructure.postgres.query_reader_provisioning import (
            reviewed_query_reader_profile,
        )

        context: dict[str, Any] = json.loads(SESSION.read_text())
        trusted = make_conninfo(
            os.environ["N1_TEST_DATABASE_URL"], dbname=context["database"]
        )
        reader = make_conninfo(
            trusted, user=context["reader"], password=context["reader_password"]
        )

        def control() -> psycopg.Connection[Any]:
            return psycopg.connect(trusted, autocommit=True)

        def restricted() -> psycopg.Connection[Any]:
            return psycopg.connect(reader, autocommit=True)

        with control() as db:

            def count(table: str) -> int:
                row = db.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(
                        sql.Identifier(*table.split("."))
                    )
                ).fetchone()
                assert row is not None
                return int(row[0])

            assert count("memoriesql_query.invocations") == 1
            assert count("memoriesql.query_result_creations") == 1
            assert count("memoriesql.query_disclosures") == 0
            profile = reviewed_query_reader_profile(db, context["reader"])
            service = PostgresAgentSqlResults(
                control_factory=control,
                reader_factory=restricted,
                authority_profile=profile,
                credential_sha256=context["credential_sha256"],
                workspace_id=UUID(context["workspace_id"]),
            )
            reply = json.loads(service.handle(json.dumps(context["request"]).encode()))
            assert reply["outcome"] == "available", reply
            again = json.loads(service.handle(json.dumps(context["request"]).encode()))
            assert again["result"] == reply["result"], (reply, again)
            assert again["access_receipt_ref"] != reply["access_receipt_ref"]
            assert count("memoriesql_query.invocations") == 1
            assert count("memoriesql.query_result_creations") == 1
            assert count("memoriesql.query_disclosures") == 2
            row = db.execute(
                "SELECT charged_db_ms,reserved_db_ms FROM memoriesql.query_deliveries "
                "WHERE outcome='abandoned'"
            ).fetchone()
            assert row is not None and row[0] == row[1] == 30000, row
        print(
            "fresh installed process redelivered the committed result twice; "
            "no SELECT redispatch; dead owner charged its full reservation"
        )
        return
    if mode == "cleanup":
        context = json.loads(SESSION.read_text())
        with psycopg.connect(os.environ["N1_TEST_DATABASE_URL"], autocommit=True) as db:
            db.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(context["database"])
                )
            )
            db.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(
                    sql.Identifier(context["reader"])
                )
            )
        SESSION.unlink()
        print("task-owned fictional query-restart database and reader removed")
        return
    raise SystemExit("mode must be prepare, recover or cleanup")


if __name__ == "__main__":
    main()
