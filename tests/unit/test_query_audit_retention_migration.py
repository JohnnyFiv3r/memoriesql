"""DB-free checks for governed query audit retention (M0047); no SQL execution proof."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
MIGRATION = MIGRATIONS / "0047_query_audit_retention.sql"
SQL = MIGRATION.read_text() if MIGRATION.exists() else ""
FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.([a-z0-9_]+)\((.*?)\)"
    r"(.*?)AS \$\$(.*?)\$\$;",
    re.DOTALL,
)
AUDIT = "memoriesql.authorization_audit_events"


def compact(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


def functions(sql: str) -> dict[str, tuple[str, str, str]]:
    return {m[1]: (m[2], m[3], m[4]) for m in FUNCTION.finditer(sql)}


def definition(sql: str, name: str) -> str:
    """The whole CREATE [OR REPLACE] FUNCTION statement of one function."""
    match = re.search(
        rf"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.{name}\(.*?\n(?:END )?\$\$;\n",
        sql,
        re.DOTALL,
    )
    assert match, name
    return match.group(0)


class QueryAuditRetentionMigration(unittest.TestCase):
    def body(self, name: str) -> str:
        self.assertIn(name, functions(SQL))
        return functions(SQL)[name][2]

    def test_kernel_writer_changes_only_by_query_attribution(self) -> None:
        before = definition(
            (MIGRATIONS / "0006_force_resource_authorization.sql").read_text(),
            "application_authorize_resource",
        )
        after = definition(SQL, "application_authorize_resource")
        block = after[
            after.index("    -- Query work is attributed") : after.index(
                "    INSERT INTO memoriesql.authorization_audit_events ("
            )
        ]
        restored = (
            after.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
            .replace("    event_id uuid := pg_catalog.uuidv7();\n", "", 1)
            .replace(block, "", 1)
            .replace(
                "        context_record.tenant_id,\n        event_id,\n",
                "        context_record.tenant_id,\n        pg_catalog.uuidv7(),\n",
                1,
            )
        )
        self.assertEqual(restored, before)
        # The attribution receives exactly the values the row is written with.
        values = compact(after[after.index(") VALUES (") :])
        for argument in (
            "CASE WHEN allowed THEN resource_record.access_scope_id ELSE NULL END",
            "CASE WHEN allowed THEN policy_revision_id ELSE NULL END",
            "CASE WHEN allowed THEN 'allowed' ELSE 'denied' END",
            "CASE WHEN allowed THEN 'current_policy_allowed' ELSE "
            "'resource_unavailable' END",
            "statement_timestamp()",
        ):
            self.assertIn(argument, compact(block))
            self.assertIn(argument, values)
        self.assertIn(") IS FALSE THEN RETURN allowed; END IF;", compact(block))

    def test_admission_refuses_only_on_the_separate_audit_allowance(self) -> None:
        before = definition(
            (MIGRATIONS / "0046_agent_sql_result_helpers.sql").read_text(),
            "admit_query_delivery_v1",
        )
        after = definition(SQL, "admit_query_delivery_v1")
        check = (
            " -- Retained query audit draws on its own workspace allowance, apart from the\n"
            " -- content allocation (owner decision 2026-10-22). Work admitted adds its audit.\n"
            " IF memoriesql.query_audit_row_bytes_v1()*(SELECT count(*) "
            "FROM memoriesql.query_audit_events\n"
            "   WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id)\n"
            "   >=memoriesql.query_audit_allowance_bytes_v1() THEN\n"
            "  RETURN jsonb_build_object('refused','audit','remaining',remaining);\n"
            " END IF;\n"
        )
        self.assertEqual(after.count(check), 1)
        self.assertEqual(after.replace(check, ""), before)
        # Every budget refusal precedes the step and delivery it would admit.
        self.assertLess(after.index("'refused','transport'"), after.index(check))
        self.assertLess(
            after.index(check), after.index("INSERT INTO memoriesql.query_steps")
        )
        self.assertIn("SELECT 2147483648::bigint", compact(SQL))
        # The 512 MiB content allocation is unchanged: no reservation change.
        self.assertNotIn("reserve_result_preparation", SQL)
        self.assertEqual(
            re.findall(r"CREATE OR REPLACE FUNCTION memoriesql\.([a-z0-9_]+)", SQL),
            ["application_authorize_resource", "admit_query_delivery_v1"],
        )

    def test_one_append_only_exception_for_the_governed_expiry(self) -> None:
        body = compact(self.body("authorization_audit_retention_guard_v1"))
        self.assertEqual(body.count("RETURN OLD;"), 1)
        self.assertIn(
            "IF TG_OP='DELETE' AND current_setting('memoriesql.query_audit_expiry',true)='on' "
            "AND EXISTS(SELECT 1 FROM memoriesql.query_audit_events e "
            "WHERE e.tenant_id=OLD.tenant_id "
            "AND e.authorization_audit_event_id=OLD.authorization_audit_event_id "
            "AND e.expires_at<=clock_timestamp()) THEN RETURN OLD; END IF;",
            body,
        )
        refusal = compact(
            functions((MIGRATIONS / "0001_create_schema_history.sql").read_text())[
                "reject_immutable_change"
            ][2]
        )
        self.assertIn(refusal.removeprefix("BEGIN ").removesuffix(" END;"), body)
        self.assertNotIn("UPDATE", body)
        self.assertIn(
            "DROP TRIGGER authorization_audit_events_immutable ON "
            "memoriesql.authorization_audit_events; "
            "CREATE TRIGGER authorization_audit_events_immutable "
            "BEFORE UPDATE OR DELETE ON memoriesql.authorization_audit_events "
            "FOR EACH ROW EXECUTE FUNCTION "
            "memoriesql.authorization_audit_retention_guard_v1();",
            compact(SQL),
        )

    def test_only_the_governed_expiry_deletes_audit_rows_or_sets_its_marker(
        self,
    ) -> None:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            text = compact(path.read_text())
            with self.subTest(migration=path.name):
                deletes = len(re.findall(rf"DELETE FROM {re.escape(AUDIT)}\b", text))
                markers = text.count("'memoriesql.query_audit_expiry','on'")
                self.assertEqual(
                    (deletes, markers),
                    (1, 1) if path == MIGRATION else (0, 0),
                )
                self.assertNotRegex(
                    text,
                    r"(?:UPDATE|TRUNCATE)\s+memoriesql\.authorization_audit_events\b",
                )
                if path != MIGRATION:
                    self.assertNotIn("DROP TRIGGER authorization_audit_events", text)
        body = compact(self.body("expire_query_audits_v1"))
        on = body.index(
            "PERFORM set_config('memoriesql.query_audit_expiry','on',true);"
        )
        delete = body.index(f"DELETE FROM {AUDIT} a USING due")
        off = body.index("PERFORM set_config('memoriesql.query_audit_expiry','',true);")
        self.assertLess(on, delete)
        self.assertLess(delete, off)
        # Only the bounded delete runs while the expiry marker is on.
        self.assertEqual(body[on:off].count(";"), 3)
        self.assertIn("WHERE e.expires_at<=started", body[on:delete])
        self.assertNotRegex(compact(SQL), r"(?i)\bTEMP(?:ORARY)?\b|pg_temp")

    def test_attribution_needs_a_live_reserved_delivery_of_the_same_principal(
        self,
    ) -> None:
        body = compact(self.body("query_audit_delivery_v1"))
        for predicate in (
            "current_setting('memoriesql.query_audit_delivery',true)",
            "claim !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            "d.workspace_id<>c.workspace_id",
            "d.state<>'reserved'",
            "NOT memoriesql.query_run_owner_v1(r,c)",
            "clock_timestamp()>=LEAST(d.deadline,r.expires_at)",
        ):
            self.assertIn(predicate, body)
        self.assertNotIn("RAISE", body)
        record = compact(self.body("record_query_audit_v1"))
        self.assertIn("IF d.delivery_ref IS NULL THEN RETURN NULL; END IF;", record)
        self.assertIn("recorded+interval '30 days'", record)
        self.assertIn(
            "ON CONFLICT(tenant_id,decision_key) DO NOTHING; RETURN FOUND;", record
        )
        self.assertIn("SELECT 2048::bigint", compact(SQL))

    def test_duplicate_key_omits_only_each_rows_own_identifiers(self) -> None:
        create = re.search(
            rf"CREATE TABLE {re.escape(AUDIT)} \((.*?)\n\);",
            (MIGRATIONS / "0005_create_principals_and_access_scopes.sql").read_text(),
            re.DOTALL,
        )
        assert create
        columns = [
            line.split()[0]
            for line in create.group(1).splitlines()
            if line.strip()
            and not line.strip().startswith("CONSTRAINT")
            and not line.startswith("        ")
        ]
        key = re.search(
            r"jsonb_build_array\((.*?)\)::text",
            compact(self.body("record_query_audit_v1")),
        )
        assert key
        # Each audit column, in order, except the tenant (the key's scope), the
        # event id and the caller's per-call request id; plus the delivery.
        names = {
            "authenticated_principal_id": "c.principal_id",
            "principal_kind": "c.principal_kind",
            "on_behalf_of_user_id": "c.on_behalf_of_user_id",
            "pairing_grant_id": "c.pairing_grant_id",
            "workspace_id": "c.workspace_id",
            "access_scope_id": "scope",
            "resource_kind": "resource_kind",
            "resource_id": "resource",
            "capability_key": "capability",
            "permission_key": "permission",
            "policy_revision_id": "policy",
            "decision": "decision",
            "decision_reason": "reason",
            "recorded_at": "recorded",
        }
        expected = ["d.delivery_ref"] + [
            names[c]
            for c in columns
            if c not in {"tenant_id", "authorization_audit_event_id", "request_id"}
        ]
        self.assertEqual([part.strip() for part in key.group(1).split(",")], expected)

    def test_no_reader_depends_on_duplicate_rows_or_query_request_ids(self) -> None:
        # C1 reader check. Kernel definitions aside, only 0013/0014 row-security
        # policies read the table, through EXISTS on a caller-set request id.
        readers: dict[str, int] = {}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            count = path.read_text().count(AUDIT)
            if count:
                readers[path.name] = count
        self.assertEqual(
            set(readers),
            {
                "0005_create_principals_and_access_scopes.sql",
                "0006_force_resource_authorization.sql",
                "0013_durable_source_range_capture.sql",
                "0014_transcript_span_fold.sql",
                MIGRATION.name,
            },
        )
        for name in (
            "0013_durable_source_range_capture.sql",
            "0014_transcript_span_fold.sql",
        ):
            text = (MIGRATIONS / name).read_text()
            joins = re.findall(
                r"(\w+)\s*\(\s*SELECT 1\s+FROM memoriesql\.current_authorization_context\(\) "
                r"AS context\s+JOIN memoriesql\.authorization_audit_events AS audit"
                r".*?AND audit\.request_id::text = pg_catalog\.current_setting\(\s*"
                r"'memoriesql\.\w+_request_id', true",
                text,
                re.DOTALL,
            )
            self.assertEqual(joins, ["EXISTS"] * text.count(AUDIT), name)
        # Every query-path writer passes a fresh per-call request id that nothing
        # reads; only 0013/0014 pass a caller's request id, outside query work.
        calls: dict[str, list[str]] = {}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for match in re.finditer(
                r"(?<!FUNCTION memoriesql\.)application_authorize_resource"
                r"\(((?:[^()]|\([^()]*\))*)\)",
                path.read_text(),
            ):
                if "requested_resource_kind" not in match.group(1):
                    calls.setdefault(path.name, []).append(
                        compact(match.group(1)).rsplit(",", 1)[-1].strip()
                    )
        self.assertEqual(
            calls.pop("0016_complete_input_evidence_packages.sql"), ["uuidv7()"] * 2
        )
        self.assertEqual(
            set(calls),
            {"0013_durable_source_range_capture.sql", "0014_transcript_span_fold.sql"},
        )
        # No later function sets such a request id for row security to match.
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name[:4] > "0014":
                self.assertNotRegex(path.read_text(), r"'memoriesql\.\w+_request_id'")

    def test_attribution_is_private_insert_only_and_default_deny(self) -> None:
        self.assertIn(
            "ALTER TABLE memoriesql.query_audit_events FORCE ROW LEVEL SECURITY", SQL
        )
        self.assertIn(
            "REVOKE ALL ON memoriesql.query_audit_events FROM "
            "PUBLIC,memoriesql_application,memoriesql_worker",
            SQL,
        )
        self.assertNotRegex(SQL, r"UPDATE memoriesql\.query_audit_events")
        self.assertNotRegex(
            SQL, r"(?i)CREATE\s+(?:ROLE|POLICY)|ALTER\s+DEFAULT\s+PRIVILEGES"
        )
        self.assertIn("ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED", SQL)
        granted = set(
            re.findall(r"GRANT EXECUTE ON FUNCTION memoriesql\.([a-z0-9_]+)", SQL)
        )
        self.assertEqual(
            granted,
            {
                "expire_query_audits_v1",
                "expire_workspace_query_audits_v1",
                "query_audit_retention_v1",
            },
        )
        own = set(functions(SQL)) - {
            "application_authorize_resource",
            "admit_query_delivery_v1",
        }
        for name in sorted(own):
            settings = functions(SQL)[name][1]
            with self.subTest(function=name):
                self.assertRegex(
                    SQL,
                    rf"REVOKE ALL ON FUNCTION memoriesql\.{name}\([^;]*\) FROM PUBLIC;",
                )
                if "SECURITY DEFINER" in settings:
                    self.assertIn("SET search_path=pg_catalog,memoriesql", settings)
                    self.assertIn("SET row_security=off", settings)

    def test_inventory_registers_forward_0047_and_keeps_0046(self) -> None:
        inventory = json.loads(
            (ROOT / "contracts/migration-inventory.json").read_text()
        )
        rows = inventory["migrations"]
        self.assertEqual([int(r["filename"][:4]) for r in rows], list(range(1, 52)))
        self.assertEqual(
            rows[46],
            {
                "filename": MIGRATION.name,
                "sha256": hashlib.sha256(SQL.encode()).hexdigest(),
            },
        )
        self.assertEqual(
            rows[45]["sha256"],
            "5fec1d180b11c8e5593c97745b9d36d08f64249d6bd3abfa646844057aec9c03",
        )


if __name__ == "__main__":
    unittest.main()
