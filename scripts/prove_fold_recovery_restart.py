"""Three separate installed processes: fictional setup, service recovery, cleanup.

Copy with the public fixture helpers into a disposable directory. The sole
handoff contains database location, service credential, workspace and source.
No command, revision, file identity, receipt key or external transcript survives.
"""

from __future__ import annotations

import json
import os
import sys
import tracemalloc
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main() -> None:
    mode = sys.argv[1]
    path = Path("fictional-recovery-session.json")
    if mode == "prepare":
        # Imported only in the setup process, never by the recovering service.
        sys.path.insert(0, str(Path.cwd()))
        if TYPE_CHECKING:
            from tests.runtime.fold_recovery_fixture import seed, service
            from tests.runtime.test_postgres_runtime import PostgresRuntime
        else:
            from fold_recovery_fixture import seed, service
            from test_postgres_runtime import PostgresRuntime
        from memoriesql.infrastructure.postgres.migration_runner import migrate

        fixture = PostgresRuntime()
        fixture.setUp()
        database: Any = fixture.db
        try:
            # Seed schema18 first: upgrading adds only recovery-order metadata.
            migrate(fixture.db, expected_current_version=14, target_version=18)
            seed(fixture, 1)
            seed(fixture, 2)
            before = database.execute(
                "SELECT jsonb_agg(to_jsonb(r) ORDER BY transcript_fold_receipt_id) FROM memoriesql.transcript_fold_receipts r"
            ).fetchone()[0]
            migrate(fixture.db, expected_current_version=18, target_version=19)
            after = database.execute(
                "SELECT jsonb_agg(to_jsonb(r)-'recovery_position' ORDER BY transcript_fold_receipt_id) FROM memoriesql.transcript_fold_receipts r"
            ).fetchone()[0]
            assert before == after
            secret, _ = service(fixture)
            path.write_text(
                json.dumps(
                    dict(
                        database=fixture.database,
                        credential_sha256=secret,
                        workspace_id=str(fixture.workspace),
                        source_object_id=str(fixture.source),
                    )
                )
            )
        except BaseException:
            fixture.cleanup()
            raise
        fixture.db.close()
        print("fictional setup exited; schema18 receipt bytes preserved")
    elif mode == "recover":
        context = json.loads(path.read_text())
        # No fixture imports. Deny external files and subprocesses in this process.
        allowed = tuple(
            Path(p).resolve() for p in (sys.prefix, sys.base_prefix, os.getcwd())
        )

        def guard(event: str, args: tuple[Any, ...]) -> None:
            if event == "subprocess.Popen":
                raise PermissionError("recovery process denies subprocesses")
            if (
                event in {"open", "os.listdir", "os.scandir"}
                and args
                and isinstance(args[0], str | bytes | os.PathLike)
            ):
                candidate = Path(os.fsdecode(args[0])).resolve()
                if not any(candidate.is_relative_to(p) for p in allowed):
                    raise PermissionError("recovery process denies external files")

        sys.addaudithook(guard)
        from memoriesql.application.fold_recovery import (
            DiscoverFoldOutcomes,
            InspectFoldOutcome,
            PageFoldLineage,
            ReadFoldEvidence,
        )
        from memoriesql.infrastructure.postgres.fold_recovery import (
            PostgresFoldRecovery,
        )

        calls = 0
        byte_count = 0
        outcomes = 0
        cursor = None
        started = monotonic()
        tracemalloc.start()
        with psycopg.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"], dbname=context["database"]
            ),
            autocommit=True,
        ) as db:
            reader = PostgresFoldRecovery(
                db,
                credential_sha256=context["credential_sha256"],
                workspace_id=UUID(context["workspace_id"]),
            )
            while True:
                page = reader.discover(
                    DiscoverFoldOutcomes(
                        source_object_id=UUID(context["source_object_id"]),
                        continuation=cursor,
                        limit=2,
                    )
                )
                calls += 1
                for summary in page.outcomes:
                    detail = reader.inspect(InspectFoldOutcome(key=summary.key))
                    calls += 1
                    outcomes += 1
                    ordinal = 0
                    while True:
                        lineage = reader.lineage(
                            PageFoldLineage(key=summary.key, next_ordinal=ordinal)
                        )
                        calls += 1
                        for part in lineage.ranges:
                            data = reader.read(
                                ReadFoldEvidence(
                                    key=summary.key,
                                    evidence="raw_lineage",
                                    lineage_ordinal=part.lineage_ordinal,
                                    expected_sha256=part.source_bytes_sha256,
                                )
                            )
                            calls += 1
                            byte_count += len(data.content)
                        if lineage.next_ordinal is None:
                            break
                        ordinal = lineage.next_ordinal
                    if detail.exact_envelope_sha256:
                        offset = 0
                        while True:
                            data = reader.read(
                                ReadFoldEvidence(
                                    key=summary.key,
                                    evidence="exact_envelope",
                                    expected_sha256=detail.exact_envelope_sha256,
                                    byte_offset=offset,
                                )
                            )
                            calls += 1
                            byte_count += len(data.content)
                            if data.next_byte_offset is None:
                                break
                            offset = data.next_byte_offset
                if page.continuation is None:
                    break
                cursor = page.continuation
        assert outcomes == 6
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(
            json.dumps(
                dict(
                    outcomes=outcomes,
                    operations=calls,
                    recovered_bytes=byte_count,
                    provider_calls=0,
                    elapsed_seconds=round(monotonic() - started, 3),
                    peak_python_bytes=peak,
                    external_transcript_access=False,
                )
            )
        )
        # Cleanup gets only the fictional database name after the service ends.
        Path("fictional-recovery-cleanup.json").write_text(
            json.dumps({"database": context["database"]})
        )
    elif mode == "cleanup":
        cleanup = Path("fictional-recovery-cleanup.json")
        context = json.loads((cleanup if cleanup.exists() else path).read_text())
        with psycopg.connect(os.environ["N1_TEST_DATABASE_URL"], autocommit=True) as db:
            db.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(context["database"])
                )
            )
        (cleanup if cleanup.exists() else path).unlink()
    else:
        raise ValueError("unknown proof stage")


if __name__ == "__main__":
    main()
