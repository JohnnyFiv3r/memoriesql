"""Admission-bypassing privilege attacks in an owned disposable fictional DB."""

from __future__ import annotations

import hashlib
import os
import unittest
from dataclasses import replace
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlAdmissionError
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QualifiedView,
    QueryAuthorityProfile,
    procedure_fingerprint,
    qualify_query_authority,
    table_authority_fingerprint,
    verify_query_login,
)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AgentSqlAuthority(unittest.TestCase):
    def setUp(self) -> None:
        self.admin = psycopg.connect(
            os.environ["N1_TEST_DATABASE_URL"], autocommit=True
        )
        self.suffix = uuid4().hex
        self.name = "pr05_authority_" + self.suffix
        self.owner = "pr05_owner_" + self.suffix
        self.reader = "pr05_reader_" + self.suffix
        self.admin.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(
                sql.Identifier(self.owner)
            )
        )
        self.addCleanup(self.drop_role, self.owner)
        self.admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD 'fictional-only' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(self.reader))
        )
        self.addCleanup(self.drop_role, self.reader)
        self.admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name))
        )
        self.addCleanup(self.drop_database)
        self.db = psycopg.connect(
            make_conninfo(os.environ["N1_TEST_DATABASE_URL"], dbname=self.name),
            autocommit=True,
        )
        self.addCleanup(self.db.close)
        self.db.execute(
            sql.SQL("REVOKE CREATE,TEMP ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(self.name)
            )
        )
        self.db.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        self.db.execute("CREATE SCHEMA memory_v1; CREATE SCHEMA pr05_private")
        self.db.execute("REVOKE ALL ON SCHEMA pr05_private FROM PUBLIC")
        self.db.execute(
            "CREATE TABLE pr05_private.capabilities (pid int, started timestamptz, tenant uuid)"
        )
        self.db.execute(
            'CREATE TABLE pr05_private.rows (id uuid PRIMARY KEY, tenant uuid, text text COLLATE "C")'
        )
        self.db.execute(
            sql.SQL("ALTER TABLE pr05_private.rows OWNER TO {}").format(
                sql.Identifier(self.owner)
            )
        )
        self.db.execute(
            "ALTER TABLE pr05_private.rows ENABLE ROW LEVEL SECURITY; ALTER TABLE pr05_private.rows FORCE ROW LEVEL SECURITY"
        )
        self.db.execute("""CREATE FUNCTION pr05_private.visible(t uuid) RETURNS bool LANGUAGE sql STABLE SECURITY DEFINER
          SET search_path=pg_catalog,pr05_private AS $$
          SELECT EXISTS (SELECT 1 FROM pr05_private.capabilities c JOIN pg_stat_activity a ON a.pid=c.pid
            WHERE c.pid=pg_backend_pid() AND c.started=a.backend_start AND c.tenant=t) $$""")
        self.db.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA pg_catalog FROM PUBLIC")
        self.db.execute(
            "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA information_schema FROM PUBLIC"
        )
        self.db.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
        self.db.execute(
            "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA pr05_private FROM PUBLIC"
        )
        self.db.execute("REVOKE UPDATE ON pg_catalog.pg_settings FROM PUBLIC")
        self.db.execute(
            "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
        )
        self.db.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            ).format(sql.Identifier(self.owner))
        )
        self.db.execute(
            sql.SQL(
                "GRANT USAGE ON SCHEMA memory_v1,pr05_private TO {},{}; GRANT EXECUTE ON FUNCTION pr05_private.visible(uuid) TO {},{}"
            ).format(
                *[
                    sql.Identifier(r)
                    for r in [self.owner, self.reader, self.owner, self.reader]
                ]
            )
        )
        self.db.execute(
            "CREATE POLICY pr05_tenant ON pr05_private.rows USING (pr05_private.visible(tenant))"
        )
        self.db.execute(
            "CREATE VIEW memory_v1.observations WITH (security_barrier=true) AS SELECT id,text FROM pr05_private.rows"
        )
        self.db.execute(
            sql.SQL(
                "ALTER VIEW memory_v1.observations OWNER TO {}; GRANT SELECT ON memory_v1.observations TO {}"
            ).format(sql.Identifier(self.owner), sql.Identifier(self.reader))
        )
        self.tenant = uuid4()
        self.allowed = uuid4()
        self.db.execute(
            "INSERT INTO pr05_private.rows VALUES (%s,%s,'allowed fictional'),(%s,%s,'other tenant')",
            (self.allowed, self.tenant, uuid4(), uuid4()),
        )
        view_oid = self.scalar("SELECT 'memory_v1.observations'::regclass::oid")
        table_oid = self.scalar("SELECT 'pr05_private.rows'::regclass::oid")
        function_oid = self.scalar(
            "SELECT 'pr05_private.visible(uuid)'::regprocedure::oid"
        )
        self.profile = QueryAuthorityProfile(
            self.reader,
            self.scalar("SELECT oid FROM pg_database WHERE datname=current_database()"),
            int(self.scalar("SHOW server_version_num")),
            frozenset(
                self.scalar("SELECT oid FROM pg_namespace WHERE nspname=%s", (name,))
                for name in [
                    "memory_v1",
                    "pr05_private",
                    "pg_catalog",
                    "information_schema",
                ]
            ),
            (
                QualifiedView(
                    view_oid,
                    self.owner,
                    hashlib.sha256(
                        self.scalar(
                            "SELECT pg_get_viewdef(%s,true)", (view_oid,)
                        ).encode()
                    ).hexdigest(),
                    (table_oid,),
                ),
            ),
            {function_oid: procedure_fingerprint(self.db, function_oid)},
            {table_oid: table_authority_fingerprint(self.db, table_oid)},
            frozenset(
                self.scalar("SELECT oid FROM pg_roles WHERE rolname=%s", (name,))
                for name in [self.owner, self.db.info.user]
            ),
        )
        self.authority = qualify_query_authority(self.db, self.profile)
        self.query = psycopg.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"],
                dbname=self.name,
                user=self.reader,
                password="fictional-only",
            ),
            autocommit=True,
        )
        self.addCleanup(self.query.close)
        verify_query_login(self.query, self.authority)

    def drop_database(self) -> None:
        self.admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(self.name)
            )
        )

    def drop_role(self, name: str) -> None:
        self.admin.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name))
        )
        if name == self.owner:
            self.admin.close()

    def scalar(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(statement, parameters).fetchone()
        assert row
        return row[0]

    def install_scope(self) -> None:
        # Only the trusted issuer connection writes capabilities. Identity binds
        # PID and start, not a caller GUC or an agent-provided tenant predicate.
        pid = self.query.info.backend_pid
        self.db.execute(
            "INSERT INTO pr05_private.capabilities SELECT pid,backend_start,%s FROM pg_stat_activity WHERE pid=%s",
            (self.tenant, pid),
        )

    def deny(self, statement: str) -> None:
        with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=statement):
            self.query.execute(statement)

    def test_scope_without_agent_tenant_predicate_and_revocation(self) -> None:
        self.assertEqual(
            self.query.execute("SELECT id,text FROM memory_v1.observations").fetchall(),
            [],
        )
        self.install_scope()
        self.assertEqual(
            self.query.execute("SELECT id,text FROM memory_v1.observations").fetchall(),
            [(self.allowed, "allowed fictional")],
        )
        self.query.execute("SET pr05.tenant='untrusted'; SET row_security=off")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.query.execute("SELECT id,text FROM memory_v1.observations")
        self.query.execute("RESET row_security")
        self.db.execute("DELETE FROM pr05_private.capabilities")
        self.assertEqual(
            self.query.execute("SELECT id,text FROM memory_v1.observations").fetchall(),
            [],
        )

    def test_native_privileges_deny_writes_temp_create_canonical_and_receipts(
        self,
    ) -> None:
        self.install_scope()
        self.query.execute("SET default_transaction_read_only=off")
        for statement in [
            "INSERT INTO pr05_private.rows VALUES (gen_random_uuid(),gen_random_uuid(),'attack')",
            "UPDATE memory_v1.observations SET text='attack'",
            "DELETE FROM memory_v1.observations",
            "TRUNCATE pr05_private.rows",
            "CREATE TABLE memory_v1.attack(id int)",
            "CREATE TEMP TABLE attack(id int)",
            "CREATE SCHEMA attack",
            "SELECT * FROM pr05_private.rows",
            "SELECT * FROM pr05_private.capabilities",
            "INSERT INTO pr05_private.capabilities VALUES (1,now(),gen_random_uuid())",
        ]:
            with self.subTest(statement=statement):
                self.deny(statement)
        self.deny(
            sql.SQL("SET ROLE {}")
            .format(sql.Identifier(self.owner))
            .as_string(self.query)
        )
        self.deny("SET SESSION AUTHORIZATION postgres")

    def test_public_procedure_closure_and_unsafe_defaults_fail_closed(self) -> None:
        for statement in [
            "SELECT pg_sleep(0)",
            "SELECT set_config('work_mem','1GB',false)",
            "SELECT pg_read_file('irrelevant')",
            "SELECT lo_import('irrelevant')",
            "SELECT pg_cancel_backend(1)",
        ]:
            with self.subTest(statement=statement):
                self.deny(statement)
        with self.db.transaction():
            self.db.execute(
                "GRANT EXECUTE ON FUNCTION pg_catalog.pg_sleep(double precision) TO PUBLIC"
            )
            with self.assertRaises(SqlAdmissionError):
                qualify_query_authority(self.db, self.profile)
            self.db.execute(
                "REVOKE EXECUTE ON FUNCTION pg_catalog.pg_sleep(double precision) FROM PUBLIC"
            )
        with self.db.transaction():
            self.db.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA memory_v1 GRANT SELECT ON TABLES TO {}"
                ).format(sql.Identifier(self.reader))
            )
            with self.assertRaises(SqlAdmissionError):
                qualify_query_authority(self.db, self.profile)
            self.db.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA memory_v1 REVOKE SELECT ON TABLES FROM {}"
                ).format(sql.Identifier(self.reader))
            )

    def test_downward_set_role_and_definition_drift_are_rejected(self) -> None:
        self.db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.reader)))
        with self.assertRaises(SqlAdmissionError):
            verify_query_login(self.db, self.authority)
        self.db.execute("RESET ROLE")
        with self.assertRaises(SqlAdmissionError):
            qualify_query_authority(self.db, replace(self.profile, procedure_hashes={}))
        with self.assertRaises(SqlAdmissionError):
            qualify_query_authority(
                self.db,
                replace(
                    self.profile,
                    views=(replace(self.profile.views[0], definition_sha256="0" * 64),),
                ),
            )
        with self.db.transaction():
            self.db.execute("ALTER TABLE pr05_private.rows NO FORCE ROW LEVEL SECURITY")
            with self.assertRaises(SqlAdmissionError):
                qualify_query_authority(self.db, self.profile)
            self.db.execute("ALTER TABLE pr05_private.rows FORCE ROW LEVEL SECURITY")
        with self.db.transaction():
            self.db.execute(
                "ALTER POLICY pr05_tenant ON pr05_private.rows USING (true)"
            )
            with self.assertRaises(SqlAdmissionError):
                qualify_query_authority(self.db, self.profile)
            self.db.execute(
                "ALTER POLICY pr05_tenant ON pr05_private.rows USING (pr05_private.visible(tenant))"
            )

    def test_userset_settings_remain_a_pending_enforcement_gate(self) -> None:
        # Preserve the discovered server limitation. This successful bypass is
        # not accepted as enforcement of the approved settings target.
        self.db.execute("REVOKE SET ON PARAMETER work_mem FROM PUBLIC")
        self.query.execute("SET work_mem='8MB'")
        self.assertEqual(self.query.execute("SHOW work_mem").fetchone(), ("8MB",))


if __name__ == "__main__":
    unittest.main()
