"""Non-inheriting members run every adapter whose transaction switches role.

Migration 0033 revokes PUBLIC EXECUTE on every catalog routine. A member of
memoriesql_application or memoriesql_worker that does not inherit their
privileges can therefore execute nothing until its transaction runs SET LOCAL
ROLE. Each case reruns an existing acceptance test at the newest packaged schema
with its product connections made by such a login; the administrator still builds
the fixtures.
"""

from __future__ import annotations

import unittest
import uuid
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.infrastructure.postgres.complete_input_execution import (
    PostgresCompleteInput,
)
from memoriesql.infrastructure.postgres.evidence_packages import (
    PostgresEvidencePackages,
)
from memoriesql.infrastructure.postgres.logical_unit_materialization import (
    PostgresLogicalUnitMaterialization,
)
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
)
from memoriesql.infrastructure.postgres.relation_lifecycle import (
    PostgresRelationLifecycle,
)
from memoriesql.infrastructure.postgres.source_range import (
    PostgresAuthorizedSourceRangeSession,
)
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)

if TYPE_CHECKING:
    from tests.runtime import test_authored_relations as authored_tests
    from tests.runtime import test_local_client_pairing as pairing_tests
    from tests.runtime import test_personal_local_initialization as initialization_tests
    from tests.runtime import test_relation_assessment as assessment_tests
    from tests.runtime import test_source_enrollment as enrollment_tests
    from tests.runtime import test_stored_bead_inspection as inspection_tests
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_authored_relations as authored_tests
    import test_local_client_pairing as pairing_tests
    import test_personal_local_initialization as initialization_tests
    import test_relation_assessment as assessment_tests
    import test_source_enrollment as enrollment_tests
    import test_stored_bead_inspection as inspection_tests
    from test_postgres_runtime import migrate

APPLICATION = ("memoriesql_application",)
APPLICATION_AND_WORKER = ("memoriesql_application", "memoriesql_worker")


def noninheriting_member(
    test: unittest.TestCase, admin_url: str, database: str, roles: tuple[str, ...]
) -> str:
    """A login that may SET ROLE to each role but inherits none; its conninfo."""
    login = "noinherit_" + uuid.uuid4().hex[:16]
    password = "fictional-noninheriting-member"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(login), sql.Literal(password)
            )
        )
        for role in roles:
            admin.execute(
                sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(
                    sql.Identifier(role), sql.Identifier(login)
                )
            )

    def drop() -> None:
        with psycopg.connect(
            make_conninfo(admin_url, dbname=database), autocommit=True
        ) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE usename=%s",
                (login,),
            )
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(login)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))

    test.addCleanup(drop)
    return make_conninfo(admin_url, dbname=database, user=login, password=password)


def at_newest_schema(db: psycopg.Connection[Any]) -> None:
    """Migrate a fixture database to the newest packaged schema."""
    row = db.execute("SELECT max(version) FROM memoriesql.schema_migrations").fetchone()
    assert row is not None
    newest = discover_migrations()[-1].version
    if row[0] < newest:
        migrate(db, expected_current_version=row[0], target_version=newest)


def member_adapters(test: Any) -> str:
    """Rebuild the fixture's product adapters on a non-inheriting member's
    connection; returns that member's conninfo for the worker's connections."""
    at_newest_schema(test.db)
    url = noninheriting_member(test, test.admin, test.database, APPLICATION_AND_WORKER)
    member = psycopg.connect(url, autocommit=True)
    test.addCleanup(member.close)
    context: dict[str, Any] = {
        "credential_sha256": test.secret_hash,
        "workspace_id": test.workspace,
    }
    test.api = PostgresEvidencePackages(member, **context)
    test.raw = PostgresAuthorizedSourceRangeSession(member, **context)
    test.materializer = PostgresLogicalUnitMaterialization(member, **context)
    test.complete = PostgresCompleteInput(member, **context)
    test.lifecycle = PostgresRelationLifecycle(member, **context)
    if hasattr(test, "assessments"):
        test.assessments = PostgresRelationAssessments(member, **context)
    return url


class NoninheritingSourceEnrollment(enrollment_tests.ExactSourceEnrollment):
    def setUp(self) -> None:
        super().setUp()
        at_newest_schema(self.db)
        self.url = noninheriting_member(
            self, self.admin_url, self.database, APPLICATION
        )


class NoninheritingClientPairing(pairing_tests.LocalClientPairing):
    def setUp(self) -> None:
        super().setUp()
        at_newest_schema(self.db)
        self.url = noninheriting_member(
            self, self.admin_url, self.database, APPLICATION
        )


class NoninheritingInitialization(initialization_tests.PersonalLocalInitialization):
    def test_noninheriting_operator_initializes(self) -> None:
        operator = noninheriting_member(self, self.admin, self.database, APPLICATION)
        secret_file = self.root / "operator-owner.secret"
        status, created = self.init(self.request, secret_file, operator)
        self.assertEqual(status, 0, created)
        self.assertFalse(created["receipt"]["replayed"])
        self.assertTrue(
            self.authenticates(
                secret_file.read_text(encoding="ascii"),
                uuid.UUID(created["receipt"]["workspace_id"]),
            )
        )


class NoninheritingStoredInspection(inspection_tests.StoredInspection):
    def reader(self) -> PostgresStoredBeadInspection:
        at_newest_schema(self.db)
        member = psycopg.connect(
            noninheriting_member(self, self.admin, self.database, APPLICATION),
            autocommit=True,
        )
        self.addCleanup(member.close)
        return PostgresStoredBeadInspection(
            member, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )


class NoninheritingAuthoredRelations(authored_tests.AuthoredRelations):
    def setUp(self) -> None:
        super().setUp()
        self.member_url = member_adapters(self)

    def connection(self) -> Any:
        return psycopg.connect(self.member_url, autocommit=True)


class NoninheritingRelationAssessment(assessment_tests.RelationAssessment):
    def setUp(self) -> None:
        super().setUp()
        self.member_url = member_adapters(self)

    def connection(self) -> Any:
        return psycopg.connect(self.member_url, autocommit=True)


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    return unittest.TestSuite(
        [
            NoninheritingSourceEnrollment(
                "test_cli_exact_source_lifecycle_uses_current_authority"
            ),
            NoninheritingClientPairing(
                "test_cli_pairs_grants_and_revokes_one_agent_under_current_authority"
            ),
            NoninheritingInitialization("test_noninheriting_operator_initializes"),
            NoninheritingStoredInspection(
                "test_inspection_preserves_authored_meaning_and_diagnostic_confidence"
            ),
            NoninheritingAuthoredRelations(
                "test_governed_relation_lifecycle_is_append_only_and_derived"
            ),
            NoninheritingRelationAssessment(
                "test_same_bead_endpoints_with_a_separate_basis_are_accepted_on_agreement"
            ),
        ]
    )


if __name__ == "__main__":
    unittest.main()
