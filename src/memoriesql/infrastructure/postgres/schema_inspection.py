from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from psycopg import Connection

SCHEMA_NAME = "memoriesql"
SNAPSHOT_CONTRACT_VERSION = 2
PRODUCT_ROLES = ("memoriesql_application", "memoriesql_worker")


def _compact_sql(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip()


def inspect_schema(connection: Connection[Any]) -> dict[str, object]:
    """Return a stable, content-free description of the memorieSQL schema."""
    relations = [
        {
            "name": row[0],
            "kind": row[1],
            "row_level_security": row[2],
            "row_level_security_forced": row[3],
        }
        for row in connection.execute(
            """
            SELECT
                class.relname,
                CASE class.relkind
                    WHEN 'r' THEN 'table'
                    WHEN 'p' THEN 'partitioned_table'
                    WHEN 'v' THEN 'view'
                    WHEN 'm' THEN 'materialized_view'
                    WHEN 'S' THEN 'sequence'
                END AS relation_kind,
                class.relrowsecurity,
                class.relforcerowsecurity
            FROM pg_catalog.pg_class AS class
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = %s
              AND class.relkind IN ('r', 'p', 'v', 'm', 'S')
            ORDER BY class.relname
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    columns = [
        {
            "relation": row[0],
            "ordinal": row[1],
            "name": row[2],
            "type": row[3],
            "nullable": not row[4],
            "default": _compact_sql(row[5]),
            "identity": row[6] or None,
            "generated": row[7] or None,
        }
        for row in connection.execute(
            """
            SELECT
                class.relname,
                attribute.attnum,
                attribute.attname,
                pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                attribute.attnotnull,
                pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid),
                attribute.attidentity,
                attribute.attgenerated
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS class
              ON class.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = class.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid = attribute.attrelid
             AND default_value.adnum = attribute.attnum
            WHERE namespace.nspname = %s
              AND class.relkind IN ('r', 'p', 'v', 'm')
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            ORDER BY class.relname, attribute.attnum
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    constraints = [
        {
            "relation": row[0],
            "name": row[1],
            "type": row[2],
            "definition": _compact_sql(row[3]),
            "deferrable": row[4],
            "initially_deferred": row[5],
        }
        for row in connection.execute(
            """
            SELECT
                class.relname,
                constraint_record.conname,
                constraint_record.contype,
                pg_catalog.pg_get_constraintdef(constraint_record.oid, true),
                constraint_record.condeferrable,
                constraint_record.condeferred
            FROM pg_catalog.pg_constraint AS constraint_record
            JOIN pg_catalog.pg_class AS class
              ON class.oid = constraint_record.conrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = %s
            ORDER BY class.relname, constraint_record.conname
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    indexes = [
        {
            "relation": row[0],
            "name": row[1],
            "unique": row[2],
            "definition": _compact_sql(row[3]),
            "predicate": _compact_sql(row[4]),
        }
        for row in connection.execute(
            """
            SELECT
                table_record.relname,
                index_record.relname,
                index_metadata.indisunique,
                pg_catalog.pg_get_indexdef(index_metadata.indexrelid),
                pg_catalog.pg_get_expr(
                    index_metadata.indpred,
                    index_metadata.indrelid,
                    true
                )
            FROM pg_catalog.pg_index AS index_metadata
            JOIN pg_catalog.pg_class AS table_record
              ON table_record.oid = index_metadata.indrelid
            JOIN pg_catalog.pg_class AS index_record
              ON index_record.oid = index_metadata.indexrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = table_record.relnamespace
            WHERE namespace.nspname = %s
            ORDER BY table_record.relname, index_record.relname
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    triggers = [
        {
            "relation": row[0],
            "name": row[1],
            "definition": _compact_sql(row[2]),
            "enabled": row[3],
        }
        for row in connection.execute(
            """
            SELECT
                class.relname,
                trigger_record.tgname,
                pg_catalog.pg_get_triggerdef(trigger_record.oid, true),
                trigger_record.tgenabled
            FROM pg_catalog.pg_trigger AS trigger_record
            JOIN pg_catalog.pg_class AS class
              ON class.oid = trigger_record.tgrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = %s
              AND NOT trigger_record.tgisinternal
            ORDER BY class.relname, trigger_record.tgname
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    constraint_triggers = [
        {
            "relation": row[0],
            "constraint_relation": row[1],
            "constraint": row[2],
            "function": row[3],
            "trigger_type": row[4],
            "enabled": row[5],
        }
        for row in connection.execute(
            """
            SELECT
                trigger_relation.relname,
                constraint_relation.relname,
                constraint_record.conname,
                procedure.proname,
                trigger_record.tgtype,
                trigger_record.tgenabled
            FROM pg_catalog.pg_trigger AS trigger_record
            JOIN pg_catalog.pg_class AS trigger_relation
              ON trigger_relation.oid = trigger_record.tgrelid
            JOIN pg_catalog.pg_namespace AS trigger_namespace
              ON trigger_namespace.oid = trigger_relation.relnamespace
            JOIN pg_catalog.pg_constraint AS constraint_record
              ON constraint_record.oid = trigger_record.tgconstraint
            JOIN pg_catalog.pg_class AS constraint_relation
              ON constraint_relation.oid = constraint_record.conrelid
            JOIN pg_catalog.pg_proc AS procedure
              ON procedure.oid = trigger_record.tgfoid
            WHERE trigger_namespace.nspname = %s
              AND trigger_record.tgisinternal
              AND trigger_record.tgconstraint <> 0
            ORDER BY
                trigger_relation.relname,
                constraint_relation.relname,
                constraint_record.conname,
                procedure.proname,
                trigger_record.tgtype
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    views = [
        {
            "name": row[0],
            "definition": _compact_sql(row[1]),
        }
        for row in connection.execute(
            """
            SELECT class.relname, pg_catalog.pg_get_viewdef(class.oid, true)
            FROM pg_catalog.pg_class AS class
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = %s
              AND class.relkind = 'v'
            ORDER BY class.relname
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    functions = [
        {
            "name": row[0],
            "arguments": row[1],
            "result": row[2],
            "volatility": row[3],
            "definition": _compact_sql(row[4]),
        }
        for row in connection.execute(
            """
            SELECT
                procedure.proname,
                pg_catalog.pg_get_function_identity_arguments(procedure.oid),
                pg_catalog.pg_get_function_result(procedure.oid),
                procedure.provolatile,
                pg_catalog.pg_get_functiondef(procedure.oid)
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = %s
            ORDER BY procedure.proname,
                     pg_catalog.pg_get_function_identity_arguments(procedure.oid)
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    policies = [
        {
            "relation": row[0],
            "name": row[1],
            "permissive": row[2],
            "roles": row[3],
            "command": row[4],
            "using": _compact_sql(row[5]),
            "check": _compact_sql(row[6]),
        }
        for row in connection.execute(
            """
            SELECT
                tablename,
                policyname,
                permissive,
                roles,
                cmd,
                qual,
                with_check
            FROM pg_catalog.pg_policies
            WHERE schemaname = %s
            ORDER BY tablename, policyname
            """,
            (SCHEMA_NAME,),
        ).fetchall()
    ]

    roles = [
        {
            "name": row[0],
            "superuser": row[1],
            "inherit": row[2],
            "create_role": row[3],
            "create_database": row[4],
            "can_login": row[5],
            "replication": row[6],
            "bypass_row_level_security": row[7],
            "member_of": row[8],
        }
        for row in connection.execute(
            """
            SELECT
                rolname,
                rolsuper,
                rolinherit,
                rolcreaterole,
                rolcreatedb,
                rolcanlogin,
                rolreplication,
                rolbypassrls,
                ARRAY(
                    SELECT granted_role.rolname
                    FROM pg_catalog.pg_auth_members AS membership
                    JOIN pg_catalog.pg_roles AS granted_role
                      ON granted_role.oid = membership.roleid
                    WHERE membership.member = product_role.oid
                    ORDER BY granted_role.rolname
                )
            FROM pg_catalog.pg_roles AS product_role
            WHERE rolname = ANY(%s)
            ORDER BY rolname
            """,
            (list(PRODUCT_ROLES),),
        ).fetchall()
    ]

    return {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "schema": SCHEMA_NAME,
        "relations": relations,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "triggers": triggers,
        "constraint_triggers": constraint_triggers,
        "views": views,
        "functions": functions,
        "policies": policies,
        "roles": roles,
    }


def canonical_snapshot_json(snapshot: dict[str, object]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def schema_snapshot_sha256(snapshot: dict[str, object]) -> str:
    payload = canonical_snapshot_json(snapshot).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
