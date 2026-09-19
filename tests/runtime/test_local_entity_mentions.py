"""Fictional atomic authored mentions through the existing revisiting worker."""
from __future__ import annotations

import asyncio
import unittest
import uuid
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai.messages import ToolCallPart

from memoriesql.application.local_entity_mentions import (
    MENTION_EXECUTION_TASK,
    ActivateMentionAuthorship,
    load_local_mentions_task_registry,
)
from memoriesql.infrastructure.postgres.migration_runner import migrate

if TYPE_CHECKING:
    from tests.runtime import test_source_revisiting as fixtures
else:
    import test_source_revisiting as fixtures


class LocalMentions(fixtures.SourceRevisiting):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=20, target_version=22)
        previous_policy = self.dispatch_policy
        self.dispatch_policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,workspace_id,access_scope_id,source_object_id,attestor_principal_id,approved_by_principal_id,qualification_evidence_sha256,created_at,expires_at,status,4 FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (self.dispatch_policy, previous_policy),
        )
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,p.queue_name FROM memoriesql.semantic_worker_claim_policies c JOIN memoriesql.semantic_task_admission_policies p ON p.task_kind=c.task_kind AND p.contract_revision=4 WHERE c.principal_id=%s AND c.contract_revision=3",
            (self.worker_principal,),
        )
        self.mentions: list[dict[str, Any]] = [{
            "entity_mention_id": str(uuid.uuid4()),
            "surface_text": "Alex",
            "local_identity_state": "ambiguous",
            "local_identity_reason": "The fictional unit does not identify which Alex.",
        }]
        self.omit_mentions = False

    def setup_mentions(self) -> None:
        self.setup_revisiting("Alex counted four fictional trees.", activate=False)
        command = ActivateMentionAuthorship(
            idempotency_key="orchard.mentions.activate",
            binding_task_id=self.bound.task_id,
            dispatch_policy_id=self.dispatch_policy,
        )
        self.activation = self.complete.activate_mentions(command)
        self.assertEqual(self.complete.activate_mentions(command).execution_task_id,
                         self.activation.execution_task_id)

    def response(self, messages: Any, info: Any) -> Any:
        response = super().response(messages, info)
        part = cast(ToolCallPart, response.parts[0])
        data = part.args_as_dict()
        if data["typed_output"] is not None and not self.omit_mentions:
            data["typed_output"]["annotations"][0]["mentions"] = self.mentions
        part.args = data
        return response

    def worker(self, callback: Any = None, *, recorder: Any = True,
               **kwargs: Any) -> Any:
        return super().worker(callback, recorder=recorder,
                              task_definition=MENTION_EXECUTION_TASK,
                              registry_factory=load_local_mentions_task_registry)

    def test_ambiguous_local_mention_is_atomic_with_accepted_receipt(self) -> None:
        self.setup_mentions()
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(self.row("SELECT surface_text,local_identity_state,local_identity_reason,start_offset,end_offset FROM memoriesql.entity_mentions"),
                         ("Alex", "ambiguous", self.mentions[0]["local_identity_reason"], None, None))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mention_resolutions"), (0,))
        self.assertEqual(self.row("SELECT r.task_contract_version FROM memoriesql.bead_versions v JOIN memoriesql.semantic_task_receipts r USING(tenant_id,semantic_task_receipt_id)"), (4,))

    def test_authored_empty_has_accepted_version_provenance(self) -> None:
        self.mentions = []
        self.setup_mentions()
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (0,))
        self.assertEqual(self.row("SELECT r.task_contract_version FROM memoriesql.bead_versions v JOIN memoriesql.semantic_task_receipts r USING(tenant_id,semantic_task_receipt_id)"), (4,))

    def test_omission_is_invalid_and_leaves_no_canonical_meaning(self) -> None:
        self.omit_mentions = True
        self.setup_mentions()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (0,))

    def test_reread_counts_usage_without_duplicate_coverage(self) -> None:
        self.setup_mentions()
        def author(messages: Any, info: Any) -> Any:
            if not self.steps:
                return self.step(messages, info, "read", self.selection(limit=5))
            return self.response(messages, info)
        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (2,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (1,))
        self.assertEqual(self.row("SELECT sum(end_character-start_character) FROM memoriesql.complete_input_exposures"), (34,))

    def test_revoked_authority_rejects_mentions_but_keeps_usage(self) -> None:
        self.setup_mentions()
        def author(messages: Any, info: Any) -> Any:
            self.db.execute("UPDATE memoriesql.evidence_producer_policies SET status='revoked' WHERE producer_policy_id=%s", (self.policy,))
            return self.response(messages, info)
        result = asyncio.run(self.worker(author).run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,))
        self.assert_no_meaning()
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (0,))

    def test_missing_trusted_delivery_cannot_accept_mentions(self) -> None:
        self.setup_mentions()
        class NoDelivery:
            async def record_received(self, **kwargs: Any) -> None:
                raise AssertionError("legacy exposure forbidden")
            async def record_delivery(self, **kwargs: Any) -> None:
                pass
        result = asyncio.run(self.worker(recorder=NoDelivery()).run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (0,))

    def test_aggregate_payload_bound_rejects_individually_valid_mentions(self) -> None:
        self.mentions = [dict(
            entity_mention_id=str(uuid.uuid4()), surface_text="🌳" * 1024,
            local_identity_state="unresolved", local_identity_reason=None,
        ) for _ in range(32)]
        self.setup_mentions()
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assert_no_meaning()
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (0,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_usage_events"), (1,))

    def test_sql_failure_rolls_back_mentions_and_replay_preserves_set(self) -> None:
        import copy
        import hashlib
        from unittest.mock import patch

        import psycopg
        from psycopg.types.json import Jsonb

        from memoriesql.application.semantic_task_contracts import canonical_json_bytes
        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
        )

        self.setup_mentions()
        original = PostgresSemanticTaskQueue.record_canonical_result
        owner = self

        def intercept(queue: Any, fence: Any, result: Any, *, recorded_at: Any) -> str:
            payload = result.typed_output.model_dump(mode="json")
            command = dict(
                contract_version=5, expected_schema_version=22,
                idempotency_key=f"semantic-apply.{fence.attempt_id}",
                tenant_id=str(fence.tenant_id), workspace_id=str(fence.workspace_id),
                access_scope_id=str(fence.access_scope_id), task_id=str(fence.task_id),
                attempt_id=str(fence.attempt_id), lease_generation=fence.lease_generation,
                task_kind=result.task_kind, contract_revision=4,
                output_contract_hash=result.output_contract_hash,
                used_evidence_refs=list(result.used_evidence_refs),
                model_run_refs=list(result.model_run_refs), payload=payload,
            )
            # Bypass Python validation deliberately to exercise SQL atomicity.
            bad = copy.deepcopy(command)
            bad["payload"]["annotations"][0]["mentions"].append(
                copy.deepcopy(bad["payload"]["annotations"][0]["mentions"][0]))
            encoded = canonical_json_bytes(bad["payload"])
            bad.update(semantic_payload_canonical_json=encoded.decode(),
                       semantic_result_hash=hashlib.sha256(encoded).hexdigest())
            with owner.assertRaises(psycopg.errors.UniqueViolation), queue._connection.transaction():
                queue._connection.execute(
                    "SELECT * FROM memoriesql.apply_semantic_annotations(%s,%s,%s,%s)",
                    (Jsonb(bad), fence.worker_id, fence.worker_instance_id, recorded_at))
            def count_rows(table: str) -> Any:
                # Test-only owner inspection: the worker cannot read mention rows.
                queue._connection.execute("SET LOCAL ROLE NONE")
                try:
                    return queue._connection.execute(
                        "SELECT count(*) FROM memoriesql." + table).fetchone()
                finally:
                    queue._connection.execute("SET LOCAL ROLE memoriesql_worker")

            for table in ("entity_mentions", "bead_versions", "bead_semantic_statements",
                          "semantic_task_receipts"):
                owner.assertEqual(count_rows(table), (0,))
            status = original(queue, fence, result, recorded_at=recorded_at)
            owner.assertEqual(original(queue, fence, result, recorded_at=recorded_at), status)
            owner.assertEqual(count_rows("entity_mentions"), (1,))
            return status

        with patch.object(PostgresSemanticTaskQueue, "record_canonical_result", intercept):
            result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        # A later insert cannot change even an empty or nonempty accepted set.
        with self.assertRaisesRegex(psycopg.Error, "accepted_bead_immutable"), self.db.transaction():
            self.db.execute(
                "INSERT INTO memoriesql.entity_mentions SELECT tenant_id,workspace_id,access_scope_id,%s,bead_version_id,bead_id,event_id,source_unit_id,surface_text,start_offset,end_offset,recorded_by_principal_id,recorded_at,local_identity_state,local_identity_reason FROM memoriesql.entity_mentions",
                (uuid.uuid4(),))


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    # Reuse O's fixture without rerunning every inherited O test as a P case.
    return unittest.TestSuite(LocalMentions(name) for name in LocalMentions.__dict__
                              if name.startswith("test_"))
