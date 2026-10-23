"""Installed ACL and RLS of the automatic checkpoint migration (C4).

The declaration case reads only the packaged migration. The database case runs
only in an assigned DB slot: it installs every migration before checkpoints
into a fictional, empty database, records the catalog, installs checkpoints,
and requires the checkpoint objects to be closed to the application and worker
roles except for the reviewed EXECUTE entry points.
"""

from __future__ import annotations

import os
import re
import unittest
import uuid
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate

CHECKPOINTS = "automatic_investigation_checkpoints"
TABLES = frozenset(
    {
        "query_investigations",
        "query_investigation_branches",
        "query_checkpoints",
        "query_checkpoint_roots",
        "query_checkpoint_holds",
        "query_result_cleanup_hold_ends",
    }
)
# The application EXECUTE entry points; no other checkpoint function is callable.
ENTRY_POINTS = frozenset(
    {
        "query_result_access_v1",
        "prepare_automatic_checkpoint_v1",
        "publish_automatic_checkpoint_v1",
        "record_checkpoint_disclosure_v1",
        "read_query_result_page_v2",
        "record_query_disclosure_v2",
    }
)
ROLES = ("memoriesql_application", "memoriesql_worker")
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
FUNCTIONS = (
    "SELECT p.oid,p.proname,p.proname||'('||oidvectortypes(p.proargtypes)||')',"
    "COALESCE(p.proacl::text,'') FROM pg_proc p "
    "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='memoriesql'"
)
RELATIONS = (
    "SELECT c.oid,c.relname,COALESCE(c.relacl::text,''),c.relrowsecurity,"
    "c.relforcerowsecurity FROM pg_class c "
    "JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname='memoriesql' AND c.relkind IN ('r','p')"
)


def checkpoints() -> tuple[int, str]:
    (found,) = [m for m in discover_migrations() if m.name == CHECKPOINTS]
    return found.version, found.path.read_text()


def declared(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """The tables a migration creates and the functions it grants to the application."""
    tables = re.findall(r"CREATE TABLE memoriesql\.([a-z0-9_]+) \(", text)
    granted = re.findall(
        r"GRANT EXECUTE ON FUNCTION memoriesql\.([a-z0-9_]+)\([^)]*\)"
        r" TO memoriesql_application;",
        text,
    )
    return frozenset(tables), frozenset(granted)


def snapshot(db: psycopg.Connection[Any]) -> dict[str, dict[str, Any]]:
    return {
        "functions": {
            row[2]: (row[0], row[1], row[3]) for row in db.execute(FUNCTIONS).fetchall()
        },
        "tables": {
            row[1]: (row[0], *row[2:]) for row in db.execute(RELATIONS).fetchall()
        },
    }


def violations(
    db: psycopg.Connection[Any], before: dict[str, dict[str, Any]]
) -> list[str]:
    """Every way the installed checkpoint objects are more open than reviewed."""
    now = snapshot(db)
    found: list[str] = []

    def granted(statement: str, *values: Any) -> bool:
        row = db.execute(statement, values).fetchone()
        return bool(row and row[0])

    added = set(now["tables"]) - set(before["tables"])
    if added != TABLES:
        found.append(f"new tables {sorted(added)} are not {sorted(TABLES)}")
    for name in sorted(added | (TABLES & set(now["tables"]))):
        oid, acl, enabled, forced = now["tables"][name]
        if not (enabled and forced):
            found.append(
                f"{name}: row level security enabled={enabled} forced={forced}"
            )
        if granted(
            "SELECT EXISTS(SELECT 1 FROM aclexplode(NULLIF(%s,'')::aclitem[]) WHERE grantee=0)",
            acl,
        ):
            found.append(f"{name}: PUBLIC holds a table privilege")
        for role in ROLES:
            for privilege in TABLE_PRIVILEGES:
                if granted(
                    "SELECT has_table_privilege(%s,%s::oid,%s)", role, oid, privilege
                ):
                    found.append(f"{name}: {role} holds {privilege}")
            if granted(
                "SELECT has_any_column_privilege(%s,%s::oid,'SELECT,INSERT,UPDATE,REFERENCES')",
                role,
                oid,
            ):
                found.append(f"{name}: {role} holds a column privilege")
    for name, (_, *state) in before["tables"].items():
        if name in now["tables"] and now["tables"][name][1:] != tuple(state):
            found.append(f"{name}: access control changed by checkpoints")
    new = {
        signature: entry
        for signature, entry in now["functions"].items()
        if signature not in before["functions"]
    }
    for name in sorted(ENTRY_POINTS):
        if [entry for entry in new.values() if entry[1] == name] == []:
            found.append(f"{name}: entry point is not a new function")
    for signature, (oid, name, acl) in sorted(new.items()):
        if not acl or granted(
            "SELECT EXISTS(SELECT 1 FROM aclexplode(%s::aclitem[]) "
            "WHERE grantee=0 AND privilege_type='EXECUTE')",
            acl,
        ):
            found.append(f"{signature}: PUBLIC can execute")
        application = granted(
            "SELECT has_function_privilege('memoriesql_application',%s::oid,'EXECUTE')",
            oid,
        )
        if application != (name in ENTRY_POINTS):
            found.append(f"{signature}: application EXECUTE is {application}")
        if granted(
            "SELECT has_function_privilege('memoriesql_worker',%s::oid,'EXECUTE')", oid
        ):
            found.append(f"{signature}: worker can execute")
    for signature, (_, _, acl) in sorted(before["functions"].items()):
        if signature in now["functions"] and now["functions"][signature][2] != acl:
            found.append(f"{signature}: execute privileges changed by checkpoints")
    return found


class CheckpointAccessDeclarations(unittest.TestCase):
    def test_reviewed_tables_and_entry_points_are_what_checkpoints_declare(
        self,
    ) -> None:
        _, text = checkpoints()
        self.assertEqual(declared(text), (TABLES, ENTRY_POINTS))


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class CheckpointAccessControl(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.name = "n1_fictional_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name))
            )
        self.db = psycopg.connect(
            make_conninfo(self.admin_url, dbname=self.name), autocommit=True
        )
        self.addCleanup(self.cleanup_database)

    def cleanup_database(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.name)
                )
            )

    def test_checkpoint_objects_are_closed_except_the_reviewed_entry_points(
        self,
    ) -> None:
        version, _ = checkpoints()
        migrate(self.db, expected_current_version=0, target_version=version - 1)
        before = snapshot(self.db)
        migrate(self.db, expected_current_version=version - 1, target_version=version)
        self.assertEqual(violations(self.db, before), [])


if __name__ == "__main__":
    unittest.main()
