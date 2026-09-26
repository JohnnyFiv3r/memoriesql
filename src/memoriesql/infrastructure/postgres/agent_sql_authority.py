"""Independent effective-privilege qualification for a real restricted login.

This is a trusted deployment preflight, not an agent API or ACL author. Qualified
definitions/OIDs come from an independently reviewed installation manifest, never
from a query request or a scan that automatically approves whatever it finds.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from memoriesql.application.agent_sql_catalog import SqlAdmissionError


@dataclass(frozen=True)
class QualifiedView:
    oid: int
    owner: str
    definition_sha256: str
    backing_tables: tuple[int, ...]


@dataclass(frozen=True)
class QueryAuthorityProfile:
    reader: str
    database_oid: int
    server_version: int
    schema_oids: frozenset[int]
    views: tuple[QualifiedView, ...]
    # Include every effectively executable procedure, including PUBLIC grants,
    # builtin/extension overloads and trusted capability predicates.
    procedure_hashes: dict[int, str]
    table_hashes: dict[int, str]
    default_creator_oids: frozenset[int]


@dataclass(frozen=True)
class QualifiedAuthority:
    reader: str
    server_version: int
    profile_sha256: str


def procedure_fingerprint(connection: Any, oid: int) -> str:
    """Canonical fingerprint also works for aggregates/window procedures."""
    row = connection.execute(
        """SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),
             pg_get_function_result(p.oid),l.lanname,p.prosrc,p.probin,
             p.prosecdef,p.provolatile,p.proleakproof,p.proconfig,p.proowner
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=%s""",
        (oid,),
    ).fetchone()
    if row is None:
        raise SqlAdmissionError("unavailable", "authority_manifest")
    return hashlib.sha256(
        json.dumps(list(row), ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def table_authority_fingerprint(connection: Any, oid: int) -> str:
    row = connection.execute(
        "SELECT relowner,relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s",
        (oid,),
    ).fetchone()
    if row is None:
        raise SqlAdmissionError("unavailable", "authority_manifest")
    policies = connection.execute(
        """SELECT polname,polcmd,polpermissive,polroles,pg_get_expr(polqual,polrelid),
                  pg_get_expr(polwithcheck,polrelid)
           FROM pg_policy WHERE polrelid=%s ORDER BY polname COLLATE \"C\"""",
        (oid,),
    ).fetchall()
    return hashlib.sha256(
        json.dumps(
            [list(row), [list(p) for p in policies]],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def qualify_query_authority(
    inspector: Any, profile: QueryAuthorityProfile
) -> QualifiedAuthority:
    """Read-only independent inspection; caller supplies the trusted inspector."""

    def refuse(construct: str) -> None:
        raise SqlAdmissionError("unavailable", construct)

    role = inspector.execute(
        """SELECT oid,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,
                  rolreplication,rolcanlogin FROM pg_roles WHERE rolname=%s""",
        (profile.reader,),
    ).fetchone()
    if not role or any(role[1:6]) or not role[6]:
        refuse("query_login")
    oid = role[0]
    if inspector.execute(
        "SELECT 1 FROM pg_auth_members WHERE member=%s", (oid,)
    ).fetchone():
        refuse("role_membership")
    database = inspector.execute(
        """SELECT oid,datdba,has_database_privilege(%s,oid,'CREATE'),
             has_database_privilege(%s,oid,'TEMP'),current_setting('server_version_num')::int
           FROM pg_database WHERE datname=current_database()""",
        (oid, oid),
    ).fetchone()
    if (
        not database
        or database[0] != profile.database_oid
        or database[1] == oid
        or any(database[2:4])
        or database[4] != profile.server_version
    ):
        refuse("database_privileges")
    schemas = inspector.execute(
        """SELECT oid,nspowner,has_schema_privilege(%s,oid,'CREATE'),
             has_schema_privilege(%s,oid,'USAGE') FROM pg_namespace
           WHERE nspname NOT LIKE 'pg_temp_%%' AND nspname NOT LIKE 'pg_toast_temp_%%'""",
        (oid, oid),
    ).fetchall()
    for schema in schemas:
        if (
            schema[1] == oid
            or schema[2]
            or (schema[3] and schema[0] not in profile.schema_oids)
        ):
            refuse("schema_privileges")
    expected = {view.oid: view for view in profile.views}
    relations = inspector.execute(
        """SELECT c.oid,n.nspname,c.relkind,c.relowner,
             has_table_privilege(%s,c.oid,'SELECT'),
             has_any_column_privilege(%s,c.oid,'SELECT'),
             has_table_privilege(%s,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'),
             has_any_column_privilege(%s,c.oid,'INSERT,UPDATE,REFERENCES')
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE c.relkind IN ('r','p','v','m','f')""",
        (oid, oid, oid, oid),
    ).fetchall()
    visible = set()
    for relation in relations:
        rel_oid, namespace, kind, owner, read, column_read, write, column_write = (
            relation
        )
        if owner == oid or write or column_write:
            refuse("object_privileges")
        # Stock PostgreSQL catalog metadata is explicitly not hidden. Its
        # readonly grants do not admit physical/system schemas to agent SQL.
        if namespace in {"pg_catalog", "information_schema"}:
            continue
        if read or column_read:
            if rel_oid not in expected or kind != "v" or not read:
                refuse("read_privileges")
            visible.add(rel_oid)
    if visible != set(expected):
        refuse("view_privileges")
    if inspector.execute(
        "SELECT 1 FROM pg_class WHERE CASE WHEN relkind='S' THEN has_sequence_privilege(%s,oid,'SELECT,USAGE,UPDATE') ELSE false END LIMIT 1",
        (oid,),
    ).fetchone():
        refuse("sequence_privileges")
    if inspector.execute(
        "SELECT 1 FROM pg_foreign_server WHERE has_server_privilege(%s,oid,'USAGE') LIMIT 1",
        (oid,),
    ).fetchone():
        refuse("foreign_server_privileges")
    for view in profile.views:
        state = inspector.execute(
            """SELECT r.rolname,r.rolsuper,r.rolbypassrls,r.rolcanlogin,
               c.reloptions,pg_get_viewdef(c.oid,true) FROM pg_class c
               JOIN pg_roles r ON r.oid=c.relowner WHERE c.oid=%s""",
            (view.oid,),
        ).fetchone()
        if (
            not state
            or state[0] != view.owner
            or any(state[1:4])
            or "security_barrier=true" not in (state[4] or [])
            or "security_invoker=true" in (state[4] or [])
        ):
            refuse("view_authority")
        if hashlib.sha256(state[5].encode()).hexdigest() != view.definition_sha256:
            refuse("view_definition")
        # Check transitive rewrite dependencies rather than trusting a supplied
        # list that could omit a physical table behind another view.
        backing = inspector.execute(
            """WITH RECURSIVE deps(oid,path) AS (
              SELECT %s::oid,ARRAY[%s::oid]
              UNION ALL SELECT d.refobjid,path||d.refobjid
              FROM deps x JOIN pg_rewrite r ON r.ev_class=x.oid
              JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=r.oid
              WHERE d.refclassid='pg_class'::regclass AND NOT d.refobjid=ANY(path)
            ) SELECT DISTINCT c.oid,c.relkind,c.relrowsecurity,c.relforcerowsecurity,
                r.rolsuper,r.rolbypassrls FROM deps d JOIN pg_class c ON c.oid=d.oid
                JOIN pg_roles r ON r.oid=c.relowner WHERE c.relkind<>'v'""",
            (view.oid, view.oid),
        ).fetchall()
        if {row[0] for row in backing} != set(view.backing_tables) or not backing:
            refuse("view_dependencies")
        if any(
            row[1] not in {"r", "p"} or not all(row[2:4]) or any(row[4:6])
            for row in backing
        ):
            refuse("forced_rls")
        if any(
            row[0] not in profile.table_hashes
            or table_authority_fingerprint(inspector, row[0])
            != profile.table_hashes[row[0]]
            for row in backing
        ):
            refuse("policy_definition")
    if set(profile.table_hashes) != {
        oid for v in profile.views for oid in v.backing_tables
    }:
        refuse("policy_manifest")
    executable = {
        row[0]
        for row in inspector.execute(
            "SELECT oid FROM pg_proc WHERE has_function_privilege(%s,oid,'EXECUTE')",
            (oid,),
        ).fetchall()
    }
    if executable != set(profile.procedure_hashes):
        refuse("procedure_privileges")
    for procedure, digest in profile.procedure_hashes.items():
        if procedure_fingerprint(inspector, procedure) != digest:
            refuse("procedure_definition")
    if inspector.execute(
        "SELECT 1 FROM pg_type WHERE typowner=%s UNION ALL SELECT 1 FROM pg_proc WHERE proowner=%s UNION ALL SELECT 1 FROM pg_largeobject_metadata WHERE lomowner=%s",
        (oid, oid, oid),
    ).fetchone():
        refuse("object_ownership")
    # A future default grant that reaches the reader invalidates the profile.
    # Effective PUBLIC/member checks above also reject grants already applied.
    if inspector.execute(
        """SELECT 1 FROM pg_default_acl a CROSS JOIN LATERAL aclexplode(a.defaclacl) x
           WHERE x.grantee IN (0,%s) LIMIT 1""",
        (oid,),
    ).fetchone():
        refuse("default_privileges")
    if not profile.default_creator_oids:
        refuse("default_creator_manifest")
    for creator in profile.default_creator_oids:
        # PostgreSQL's implicit function default is PUBLIC EXECUTE even when
        # there is no pg_default_acl row; an absent row is not a safe default.
        if inspector.execute(
            """SELECT 1 FROM aclexplode(COALESCE(
                (SELECT defaclacl FROM pg_default_acl WHERE defaclrole=%s
                 AND defaclnamespace=0 AND defaclobjtype='f'),acldefault('f',%s))) a
                 WHERE a.grantee IN (0,%s) LIMIT 1""",
            (creator, creator, oid),
        ).fetchone():
            refuse("default_procedure_privileges")
    encoded = {
        "reader": profile.reader,
        "database_oid": profile.database_oid,
        "server_version": profile.server_version,
        "schemas": sorted(profile.schema_oids),
        "views": [
            {
                "oid": v.oid,
                "owner": v.owner,
                "sha256": v.definition_sha256,
                "backing": list(v.backing_tables),
            }
            for v in profile.views
        ],
        "procedures": {str(k): v for k, v in sorted(profile.procedure_hashes.items())},
        "tables": {str(k): v for k, v in sorted(profile.table_hashes.items())},
        "default_creators": sorted(profile.default_creator_oids),
    }
    digest = hashlib.sha256(
        json.dumps(
            encoded, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return QualifiedAuthority(profile.reader, profile.server_version, digest)


def verify_query_login(connection: Any, authority: QualifiedAuthority) -> None:
    """A privileged login SET ROLE downward can never pass this check."""
    row = connection.execute("SELECT session_user,current_user").fetchone()
    if (
        not row
        or row != (authority.reader, authority.reader)
        or connection.info.server_version != authority.server_version
    ):
        raise SqlAdmissionError("unavailable", "query_session_identity")
