"""DB-free checks for the forward helper substrate; no SQL execution proof."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/0046_agent_sql_result_helpers.sql"
SQL = MIGRATION.read_text() if MIGRATION.exists() else ""
FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.([a-z0-9_]+)\((.*?)\)"
    r"(.*?)AS \$\$(.*?)\$\$;",
    re.DOTALL,
)


def compact(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


def functions(sql: str) -> dict[str, tuple[str, str, str]]:
    return {m[1]: (m[2], m[3], m[4]) for m in FUNCTION.finditer(sql)}


class HelperMigrationTests(unittest.TestCase):
    def body(self, name: str) -> str:
        self.assertIn(name, functions(SQL))
        return functions(SQL)[name][2]

    def test_admission_changes_only_the_closed_operation_kinds(self) -> None:
        original = (ROOT / "migrations/0038_query_result_access.sql").read_text()
        before = functions(original)["admit_query_delivery_v1"]
        self.assertIn("admit_query_delivery_v1", functions(SQL))
        after = functions(SQL)["admit_query_delivery_v1"]
        restored = after[2].replace(
            "requested_kind NOT IN ('query','reuse_result','inspect','hydrate_source')",
            "requested_kind NOT IN ('query','reuse_result')",
        )
        self.assertEqual(
            tuple(map(compact, before)), tuple(map(compact, (*after[:2], restored)))
        )
        self.assertEqual(
            re.findall(r"CREATE OR REPLACE FUNCTION memoriesql\.([a-z0-9_]+)", SQL),
            ["admit_query_delivery_v1"],
        )

    def test_backend_declarations_match_the_real_executor_probe(self) -> None:
        source = ROOT / "src/memoriesql/infrastructure/postgres/agent_sql_results.py"
        module = ast.parse(source.read_text())
        signatures = next(
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "_HELPER_BACKEND_SIGNATURES"
                for t in node.targets
            )
        )
        declarations = {
            "memoriesql."
            + name
            + "("
            + ",".join(
                parameter.strip()
                .split(" ", 1)[1]
                .replace("timestamptz", "timestamp with time zone")
                for parameter in args.split(",")
                if parameter.strip()
            )
            + ")"
            for name, (args, _settings, _body) in functions(SQL).items()
            if name != "admit_query_delivery_v1"
        }
        self.assertEqual(len(signatures), 10)
        self.assertEqual(set(signatures), declarations)
        self.assertEqual(
            len(functions(SQL)["record_query_helper_disclosure_v1"][0].split(",")), 12
        )

    def test_private_definers_and_cursors_keep_default_deny(self) -> None:
        self.assertEqual(len(functions(SQL)), 11)
        for name, (_args, settings, _body) in functions(SQL).items():
            with self.subTest(function=name):
                self.assertIn("SECURITY DEFINER", settings)
                self.assertIn("SET search_path=pg_catalog,memoriesql", settings)
                self.assertIn("SET row_security=off", settings)
                self.assertRegex(
                    SQL,
                    rf"REVOKE ALL ON FUNCTION memoriesql\.{name}\([^;]*\) FROM PUBLIC;",
                )
        self.assertIn(
            "ALTER TABLE memoriesql.query_helper_cursors FORCE ROW LEVEL SECURITY", SQL
        )
        self.assertIn(
            "REVOKE ALL ON memoriesql.query_helper_cursors FROM PUBLIC,memoriesql_application",
            SQL,
        )
        granted = re.findall(r"GRANT EXECUTE ON FUNCTION memoriesql\.([a-z0-9_]+)", SQL)
        self.assertEqual(
            set(granted),
            {
                "admit_query_delivery_v1",
                "prepare_query_helper_v1",
                "query_helper_preparation_v1",
                "read_query_helper_artifact_v1",
                "record_query_helper_disclosure_v1",
            },
        )
        self.assertNotRegex(
            SQL, r"(?i)CREATE\s+(?:ROLE|POLICY)|ALTER\s+DEFAULT\s+PRIVILEGES"
        )

    def test_helper_step_binding_and_cleanup_use_existing_run_expiry(self) -> None:
        self.assertIn("query_steps_helper_kind_check", SQL)
        self.assertIn("query_steps_helper_complete_check", SQL)
        self.assertIn("FOREIGN KEY(tenant_id,helper_artifact_ref)", SQL)
        self.assertIn("ALTER COLUMN result_id DROP NOT NULL", SQL)
        self.assertIn("query_helper_clear_pin BEFORE UPDATE OF purged_at", SQL)
        self.assertIn("query_helper_purge AFTER UPDATE OF purged_at", SQL)
        body = self.body("purge_query_helper_step_v1")
        self.assertLess(
            body.index("NEW.helper_artifact_ref:=NULL"), body.index("DELETE FROM")
        )
        self.assertLess(
            body.index("DELETE FROM memoriesql.query_helper_cursors"),
            body.index("DELETE FROM memoriesql.result_preparation_artifacts"),
        )
        self.assertIn("AND run_ref=NEW.run_ref", body)
        self.assertIn("state='discarded',request_fingerprint=NULL", body)
        self.assertIn("encoded_bytes=0,allocation_bytes=8192", body)
        self.assertNotIn("new_allocation_bytes=", body)
        self.assertNotRegex(
            SQL, r"CREATE OR REPLACE FUNCTION memoriesql\.(?:purge_|settle_|abandon_)"
        )

    def test_delivery_uses_full_current_run_owner_and_deadline(self) -> None:
        body = self.body("query_helper_delivery_v1")
        for predicate in (
            "c:=memoriesql.result_preparation_authority_v1()",
            "NOT memoriesql.query_run_owner_v1(r,c)",
            "s.purged_at IS NOT NULL",
            "memoriesql.query_run_closures",
            "d.state<>'reserved'",
            "LEAST(d.deadline,r.expires_at)",
        ):
            self.assertIn(predicate, body)
        self.assertLess(
            body.index("result_preparation_authority_v1"), body.index("SELECT * INTO d")
        )
        self.assertLess(body.index("query_run_owner_v1"), body.index("RETURN d"))

    def test_run_purge_can_clear_shared_artifact_pins_in_any_step_order(self) -> None:
        # A cursor step pins the original step's artifact. Run expiry updates
        # all steps in one transaction; a row trigger can delete the artifact
        # before a later row's BEFORE trigger has cleared its pin.
        self.assertRegex(
            SQL,
            r"FOREIGN KEY\(tenant_id,helper_artifact_ref\)\s*"
            r"REFERENCES memoriesql\.result_preparation_artifacts\(tenant_id,artifact_ref\)\s*"
            r"DEFERRABLE INITIALLY DEFERRED;",
        )
        original = (ROOT / "migrations/0038_query_result_access.sql").read_text()
        purge = functions(original)["purge_query_run_v1"][2]
        self.assertIn(
            "UPDATE memoriesql.query_steps SET request_fingerprint=NULL", purge
        )
        self.assertIn("WHERE tenant_id=t AND run_ref=run AND purged_at IS NULL", purge)
        self.assertNotIn("COMMIT", purge)

    def test_replay_reauthorizes_original_records_without_rediscovery(self) -> None:
        body = self.body("read_query_helper_artifact_v1")
        for predicate in (
            "o.ownership_ref IS DISTINCT FROM ownership",
            "o.run_ref<>d.run_ref",
            "o.state<>'sealed'",
            "o.request_fingerprint IS DISTINCT FROM s.request_fingerprint",
            "a.artifact_sha256 IS DISTINCT FROM",
            "current_records IS DISTINCT FROM item->'manifest'",
            "query_saved_records_authorized_v1(saved_records,(item->>'known_at')::timestamptz)",
            "check_query_result_closure_v2",
            "revisiting_source_authorize",
            "p.inventory_hash=item->>'inventory_sha256'",
            "pp.inventory=item->'inventory'",
        ):
            self.assertIn(predicate, body)
        self.assertNotIn("prepare_query_sql_population", body)
        self.assertNotIn("seal_result_preparation", body)
        self.assertLess(
            body.rindex("query_helper_delivery_v1(delivery)"),
            body.index("RETURN jsonb"),
        )

    def test_frame_records_are_canonical_text_not_json_numbers(self) -> None:
        # Retained rows carry numeric SQL values (author_confidence); private
        # preparation partitions refuse JSON floats, so rows stay opaque text.
        prepared = compact(self.body("prepare_query_inspection_v1"))
        self.assertIn(
            "'records_base64',translate(encode(convert_to("
            "memoriesql.lifecycle_canonical_json_v1(",
            prepared,
        )
        self.assertNotIn("'records',(SELECT", prepared)
        replay = compact(self.body("read_query_helper_artifact_v1"))
        for predicate in (
            "jsonb_typeof(item->'records_base64') IS DISTINCT FROM 'string'",
            "records_text IS DISTINCT FROM "
            "memoriesql.lifecycle_canonical_json_v1(pinned_records)",
            "FOR record_pin IN SELECT value FROM jsonb_array_elements(pinned_records)",
            # Undecodable retained text is unavailable, never a caller error.
            "EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value "
            "OR character_not_in_repertoire THEN "
            "RAISE EXCEPTION 'query_helper_unavailable' USING ERRCODE='42501'; END;",
        ):
            self.assertIn(predicate, replay)
        self.assertNotIn("item->'records'", replay)

    def test_disclosure_publishes_receipt_refs_and_accounting_together(self) -> None:
        body = self.body("record_query_helper_disclosure_v1")
        for predicate in (
            "query_helper_delivery_v1(delivery)",
            "o.run_ref IS DISTINCT FROM d.run_ref",
            "o.state IS DISTINCT FROM 'sealed'",
            "clock_timestamp()>=q.expires_at",
            "digest IS DISTINCT FROM encode(sha256(a.content_bytes),'hex')",
            "transport_bytes>d.reserved_transport",
            "next_item<>page_first+page_count",
            "EXCEPT ALL",
            "verified#>'{content,refs}'",
            "s.helper_page_first IS DISTINCT FROM page_first",
            "s.helper_page_count IS DISTINCT FROM page_count",
            "s.helper_page_cursor IS DISTINCT FROM issued_cursor",
            "invalid_query_helper_cursor_binding",
        ):
            self.assertIn(predicate, body)
        receipt = body.index("INSERT INTO memoriesql.query_disclosures")
        visible = body.index("INSERT INTO memoriesql.query_visible_refs")
        settled = body.index("UPDATE memoriesql.query_deliveries SET state='settled'")
        self.assertLess(body.index("FOR UPDATE"), receipt)
        self.assertLess(receipt, visible)
        self.assertLess(visible, settled)
        self.assertIn("charged_db_ms=observed_ms", body)
        self.assertIn("charged_transport=transport_bytes", body)
        self.assertNotIn("COMMIT", body)

    def test_explicit_targets_and_facets_are_authorized_before_population(self) -> None:
        body = self.body("prepare_query_inspection_v1")
        preflight = body[
            : body.index("population:=memoriesql.prepare_query_sql_population_v2")
        ]
        for predicate in (
            "query_bead_records_v1",
            "relation_assertion_records_v2",
            "check_query_result_closure_v2",
            "revisiting_source_authorize",
            "relation_task_population_records_v1",
            "memoriesql.bead_supersessions",
            "sv.authored_at<=known",
        ):
            self.assertIn(predicate, preflight)
        self.assertLess(
            preflight.index("relation_task_population_records_v1"),
            preflight.index("helper_target_facet_unimplemented"),
        )

    def test_relation_read_mode_matches_current_kernel_instead_of_old_owner_wrapper(
        self,
    ) -> None:
        body = self.body("prepare_query_inspection_v1")
        preflight = body[
            : body.index("population:=memoriesql.prepare_query_sql_population_v2")
        ]
        self.assertIn(
            "WHEN memoriesql.current_context_has_capability('source.raw.read') THEN 'owner'",
            preflight,
        )
        self.assertIn(
            "WHEN c.principal_kind='agent' AND c.pairing_grant_id IS NOT NULL THEN 'agent'",
            preflight,
        )
        self.assertNotIn("memoriesql.relation_assertion_records_v1(", body)
        self.assertIn(
            "relation_assertion_records_v2(c.tenant_id,'assessed',(item#>>'{}')::uuid,known,relation_mode)",
            preflight,
        )
        self.assertIn(
            "NOT memoriesql.current_context_has_capability('source.raw.read')",
            preflight,
        )

    def test_hydration_authorizes_all_selections_before_interval_and_size_diagnostics(
        self,
    ) -> None:
        body = self.body("prepare_query_hydration_v1")
        boundary = body.index(
            "FOR resolved IN SELECT value FROM jsonb_array_elements(planned)"
        )
        preflight = body[:boundary]
        self.assertIn("resolve_query_helper_evidence_v1", preflight)
        self.assertIn("revisiting_source_authorize(source)", preflight)
        self.assertNotIn("invalid_query_helper_interval", preflight)
        for predicate in (
            "stored_bead_evidence_content",
            "end_at-start_at>16384",
            "char_length(content)",
            "octet_length(body_bytes)",
            "amount>(request->>'max_bytes')::integer",
            "'returned_sha256',encode(sha256(body_bytes),'hex')",
        ):
            self.assertIn(predicate, body[boundary:])

    def test_cursor_replay_binds_run_scope_artifact_and_original_page(self) -> None:
        body = self.body("prepare_query_helper_v1")
        for predicate in (
            "request->>'run_ref' IS DISTINCT FROM d.run_ref::text",
            "request->>'step_key' IS DISTINCT FROM d.step_key::text",
            "x.run_ref=d.run_ref",
            "request-ARRAY['step_key','cursor']",
            "read_query_helper_artifact_v1(delivery,o.operation_ref,o.ownership_ref)",
            "x.content_digest IS DISTINCT FROM encode(sha256(a.content_bytes),'hex')",
            "'page_first',x.next_item",
        ):
            self.assertIn(predicate, body)
        refusal = body.index("RAISE EXCEPTION 'query_helper_cursor_invalid'")
        self.assertLess(
            body.index("PERFORM memoriesql.prepare_query_inspection_v1"), refusal
        )
        self.assertLess(
            body.index("PERFORM memoriesql.prepare_query_hydration_v1"), refusal
        )

    def test_inventory_adds_only_forward_migration_and_real_helper_module(self) -> None:
        inventory = json.loads(
            (ROOT / "contracts/migration-inventory.json").read_text()
        )
        migrations = inventory["migrations"]
        self.assertEqual(inventory["default_policy"], "deny")
        self.assertEqual(
            [int(m["filename"][:4]) for m in migrations[:46]], list(range(1, 47))
        )
        self.assertEqual(
            migrations[45],
            {
                "filename": MIGRATION.name,
                "sha256": hashlib.sha256(SQL.encode()).hexdigest(),
            },
        )
        self.assertEqual(
            migrations[44],
            {
                "filename": "0045_legacy7_execution_unavailable.sql",
                "sha256": "88bbda99e6c4f3cb21bbd388fc22c6be84de856dafba3f4c978f3d4ee77c8f43",
            },
        )
        runtime = json.loads((ROOT / "contracts/runtime-inventory.json").read_text())
        self.assertEqual(runtime["default_policy"], "deny")
        self.assertEqual(
            sum(
                item["path"] == "src/memoriesql/application/agent_sql_helpers.py"
                for item in runtime["runtime"]
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
