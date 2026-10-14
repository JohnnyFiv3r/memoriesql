"""Trusted operator provisioning of the restricted SQL reader and its profile.

This is an installation step for the host that runs the trusted executor, never
an agent operation. Privileges come from the reviewed specification below, not
from whatever the database currently allows, and the executor independently
re-qualifies the resulting profile before every invocation. The reader password
is supplied by the host, never logged, and must stay inaccessible to agents.
"""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg import Connection, sql

from memoriesql.application.agent_sql_results import PREPARED_RELATIONS
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QualifiedView,
    QueryAuthorityProfile,
    procedure_fingerprint,
    table_authority_fingerprint,
)

VIEW_OWNER = "memoriesql_query_view_owner"
INVOCATION_PREDICATES = (
    "memoriesql_query.invocation_visible_v1(uuid)",
    "memoriesql_query.active_invocation_v1()",
)
# The reviewed builtin closure used by admitted SQL, witness lowering and native
# CYCLE paths. Agents cannot name these directly beyond closed admission.
REVIEWED_BUILTINS = (
    "jsonb_object_field_text(jsonb,text)",
    "texteq(text,text)",
    "uuid_eq(uuid,uuid)",
    "count()",
    'count("any")',
    "sum(bigint)",
    "sum(numeric)",
    "row_number()",
    "rank()",
    "dense_rank()",
    "lag(anyelement,integer)",
    "lag(anycompatible,integer,anycompatible)",
    "lead(anyelement,integer)",
    "lead(anycompatible,integer,anycompatible)",
    'jsonb_build_array("any")',
    "jsonb_build_array()",
    "jsonb_agg(anyelement)",
    "avg(bigint)",
    "avg(numeric)",
    "min(bigint)",
    "max(bigint)",
    "min(numeric)",
    "max(numeric)",
    "min(text)",
    "max(text)",
    "min(timestamptz)",
    "max(timestamptz)",
    "bool_and(boolean)",
    "bool_or(boolean)",
    "int8(integer)",
    "int8(smallint)",
    "jsonb_eq(jsonb,jsonb)",
    "record_eq(record,record)",
    "array_cat(anycompatiblearray,anycompatiblearray)",
    "int8eq(bigint,bigint)",
    "int8ne(bigint,bigint)",
    "int8lt(bigint,bigint)",
    "int8le(bigint,bigint)",
    "int8gt(bigint,bigint)",
    "int8ge(bigint,bigint)",
    "int8pl(bigint,bigint)",
    "int8mi(bigint,bigint)",
    "int8mul(bigint,bigint)",
    "int8div(bigint,bigint)",
    "numeric_eq(numeric,numeric)",
    "numeric_ne(numeric,numeric)",
    "numeric_lt(numeric,numeric)",
    "numeric_le(numeric,numeric)",
    "numeric_gt(numeric,numeric)",
    "numeric_ge(numeric,numeric)",
    "numeric_add(numeric,numeric)",
    "numeric_sub(numeric,numeric)",
    "numeric_mul(numeric,numeric)",
    "numeric_div(numeric,numeric)",
    "booleq(boolean,boolean)",
    "timestamptz_eq(timestamptz,timestamptz)",
    "lower(text)",
    "upper(text)",
    "date_trunc(text,timestamptz,text)",
    "numeric(bigint)",
    "int8(numeric)",
    "text(boolean)",
    # From nine bound values PostgreSQL probes `= ANY` and `IN` through a hash
    # table, which calls the type's default hash function. These are the six
    # types a public relation produces; float8's stays out with its comparisons.
    "hashtext(text)",
    "uuid_hash(uuid)",
    "hashint8(bigint)",
    "hash_numeric(numeric)",
    "timestamptz_hash(timestamptz)",
    "hashbool(boolean)",
)


def _scalar(
    connection: Connection[Any], text: str, values: tuple[Any, ...] = ()
) -> Any:
    row = connection.execute(text, values).fetchone()
    if row is None:
        raise RuntimeError("reviewed reader specification unavailable")
    return row[0]


def provision_query_reader(
    admin: Connection[Any], reader: str, password: str
) -> QueryAuthorityProfile:
    """Create the restricted LOGIN with exactly the reviewed privilege closure.

    Run once by an operator on a migrated database (schema 38). The role gets no
    memberships, ownership, CREATE/TEMP or table access beyond the fourteen
    prepared security-barrier views and the reviewed function closure. The
    password travels in `CREATE ROLE`; disable statement logging for this call.
    """
    if not reader.isidentifier() or not reader.islower() or len(password) < 16:
        raise ValueError("invalid reader provisioning request")
    role = sql.Identifier(reader)
    admin.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT "
            "NOCREATEDB NOCREATEROLE NOREPLICATION"
        ).format(role, sql.Literal(password))
    )
    admin.execute(sql.SQL("ALTER ROLE {} SET temp_file_limit='64MB'").format(role))
    admin.execute(
        sql.SQL("GRANT USAGE ON SCHEMA memory_v1,memoriesql_query TO {}").format(role)
    )
    for name in sorted(PREPARED_RELATIONS):
        schema, relation = name.split(".")
        admin.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(
                sql.Identifier(schema, relation), role
            )
        )
    grant_reviewed_closure(admin, reader)
    return reviewed_query_reader_profile(admin, reader)


def grant_reviewed_closure(admin: Connection[Any], reader: str) -> None:
    """Grant a provisioned reader the reviewed function closure; idempotent.

    A release that widens the closure is paired with this step on every host
    whose reader already exists. Qualification requires the reader's executable
    functions to equal the reviewed set exactly, so run it with the upgrade,
    before the reader's profile is rebuilt and before the first query.
    """
    if not reader.isidentifier() or not reader.islower():
        raise ValueError("invalid reader grant request")
    role = sql.Identifier(reader)
    for signature in INVOCATION_PREDICATES:
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION " + signature + " TO {}").format(role)
        )
    for signature in REVIEWED_BUILTINS:
        admin.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION pg_catalog." + signature + " TO {}"
            ).format(role)
        )


def reviewed_query_reader_profile(
    connection: Connection[Any], reader: str
) -> QueryAuthorityProfile:
    """The profile the executor re-qualifies; built from the reviewed spec only.

    Signatures and views come from this module, not from a scan of whatever the
    database grants. Any drift in grants, definitions or policies is refused by
    the executor's independent qualification before dispatch.
    """
    procedures = [
        _scalar(connection, "SELECT %s::regprocedure::oid", ("pg_catalog." + s,))
        for s in REVIEWED_BUILTINS
    ] + [
        _scalar(connection, "SELECT %s::regprocedure::oid", (s,))
        for s in INVOCATION_PREDICATES
    ]
    table = _scalar(
        connection, "SELECT 'memoriesql_query.population_rows'::regclass::oid"
    )
    views = []
    for name in sorted(PREPARED_RELATIONS):
        oid = _scalar(connection, "SELECT %s::regclass::oid", (name,))
        definition = _scalar(connection, "SELECT pg_get_viewdef(%s::oid,true)", (oid,))
        views.append(
            QualifiedView(
                oid,
                VIEW_OWNER,
                hashlib.sha256(definition.encode()).hexdigest(),
                (table,),
            )
        )
    return QueryAuthorityProfile(
        reader=reader,
        database_oid=_scalar(
            connection, "SELECT oid FROM pg_database WHERE datname=current_database()"
        ),
        server_version=connection.info.server_version,
        schema_oids=frozenset(
            _scalar(connection, "SELECT oid FROM pg_namespace WHERE nspname=%s", (n,))
            for n in (
                "memory_v1",
                "memoriesql_query",
                "pg_catalog",
                "information_schema",
            )
        ),
        views=tuple(views),
        procedure_hashes={
            oid: procedure_fingerprint(connection, oid) for oid in procedures
        },
        table_hashes={table: table_authority_fingerprint(connection, table)},
        default_creator_oids=frozenset(
            _scalar(connection, "SELECT oid FROM pg_roles WHERE rolname=%s", (r,))
            for r in (connection.info.user, VIEW_OWNER)
        ),
    )
