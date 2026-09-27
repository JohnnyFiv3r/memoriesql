"""Explicit seen-fixture provisioning, never a production manifest approval."""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg import Connection, sql

from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QualifiedView,
    QueryAuthorityProfile,
    procedure_fingerprint,
    table_authority_fingerprint,
)


def provision_fictional_reader(
    db: Connection[Any], reader: str
) -> QueryAuthorityProfile:
    def scalar(text: str, values: tuple[Any, ...] = ()) -> Any:
        row = db.execute(text, values).fetchone()
        assert row is not None
        return row[0]

    db.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD 'fictional-only' NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION"
        ).format(sql.Identifier(reader))
    )
    db.execute(
        sql.SQL("ALTER ROLE {} SET temp_file_limit='64MB'").format(
            sql.Identifier(reader)
        )
    )
    db.execute(
        sql.SQL(
            "GRANT USAGE ON SCHEMA memory_v1,memoriesql_query TO {}; GRANT SELECT ON ALL TABLES IN SCHEMA memory_v1 TO {}"
        ).format(sql.Identifier(reader), sql.Identifier(reader))
    )
    db.execute(
        sql.SQL(
            "GRANT EXECUTE ON FUNCTION memoriesql_query.invocation_visible_v1(uuid),memoriesql_query.active_invocation_v1() TO {}"
        ).format(sql.Identifier(reader))
    )
    # Explicit reviewed fixture signatures, not automatic function discovery.
    signatures = (
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
    )
    proc_oids = [
        scalar("SELECT %s::regprocedure::oid", ("pg_catalog." + signature,))
        for signature in signatures
    ]
    for signature in signatures:
        db.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION pg_catalog." + signature + " TO {}"
            ).format(sql.Identifier(reader))
        )
    proc_oids.append(
        scalar(
            "SELECT 'memoriesql_query.invocation_visible_v1(uuid)'::regprocedure::oid"
        )
    )
    proc_oids.append(
        scalar("SELECT 'memoriesql_query.active_invocation_v1()'::regprocedure::oid")
    )
    table = scalar("SELECT 'memoriesql_query.population_rows'::regclass::oid")
    views = db.execute(
        "SELECT c.oid,pg_get_viewdef(c.oid,true) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='memory_v1' ORDER BY c.relname"
    ).fetchall()
    return QueryAuthorityProfile(
        reader=reader,
        database_oid=scalar(
            "SELECT oid FROM pg_database WHERE datname=current_database()"
        ),
        server_version=db.info.server_version,
        schema_oids=frozenset(
            scalar("SELECT oid FROM pg_namespace WHERE nspname=%s", (n,))
            for n in (
                "memory_v1",
                "memoriesql_query",
                "pg_catalog",
                "information_schema",
            )
        ),
        views=tuple(
            QualifiedView(
                v[0],
                "memoriesql_query_view_owner",
                hashlib.sha256(v[1].encode()).hexdigest(),
                (table,),
            )
            for v in views
        ),
        procedure_hashes={oid: procedure_fingerprint(db, oid) for oid in proc_oids},
        table_hashes={table: table_authority_fingerprint(db, table)},
        default_creator_oids=frozenset(
            scalar("SELECT oid FROM pg_roles WHERE rolname=%s", (r,))
            for r in (db.info.user, "memoriesql_query_view_owner")
        ),
    )
