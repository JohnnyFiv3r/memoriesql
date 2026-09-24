"""Fictional relation assessment after acceptance (schema 29).

Every author and specialist outcome here is a fictional double; no provider is
called, and code never judges whether a relation is true. Each fictional bead's one
unit retains all of its facts and qualifications as source, and the source-backed
tests assert that both the author and the specialist received them.

The shapes the first proof's binding cut requires, and the tests that prove them:

- endpoints in two beads other than the subject, with the reporter's statement as
  an attributed basis: ``test_foreign_endpoints_carry_the_reporter_as_an_attributed_basis``;
- both endpoints in one bead, with a separate basis statement and the source's
  stated conditions as qualification:
  ``test_same_bead_endpoints_with_a_separate_basis_are_accepted_on_agreement`` and
  the first variant of ``test_a_qualifier_in_the_packet_is_used_and_an_absent_one_is_an_abstention``;
- explicit abstention: no fit and ambiguity in
  ``test_abstentions_record_every_pair_without_a_specialist_call``, and insufficient
  evidence when a needed qualifier is outside the authorized scope in the second
  variant of the qualifier test;
- authority rechecked for endpoints, basis statements and candidates:
  ``test_an_endpoint_revoked_before_apply_pauses_the_task_and_changes_nothing`` for
  the worker, and the three ``test_an_origin_losing_*`` tests for the activating
  origin alone, before any dispatch, before the specialist and at apply.

The remaining tests cover agreement on the exact assertion, disagreement left
unaccepted, reconsideration, not-assessed pairs, same-write direction correction,
one cycle check over both relation kinds, later type revisions, bead-level roots,
supervised dispatch and tenant isolation.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import psycopg
from psycopg.types.json import Jsonb
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from memoriesql.application import relation_assessment as ra
from memoriesql.application.authored_relations import RelationTypePin
from memoriesql.application.managed_dispatch import (
    ManagedDispatchTarget,
    ManagedUsageObservation,
    SupervisedQualification,
    SupervisedRoute,
)
from memoriesql.application.model_accounting import UsageProvenance
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.relation_assessment import (
    RELATION_ASSESSMENT_TASK,
    SPECIALIST_AGENT,
    SPECIALIST_INPUT,
    SPECIALIST_KEY,
    SPECIALIST_OUTPUT,
    SPECIALIST_PROFILE,
    ActivateRelationAssessment,
    RelationSpecialistDecision,
    RelationSpecialistPacket,
    load_relation_assessment_task_registry,
)
from memoriesql.application.relation_inspection import (
    InspectBeadRelationsV2,
    InspectRelationVocabulary,
)
from memoriesql.application.relation_lifecycle import (
    DecideRelationType,
    ProposeRelationType,
)
from memoriesql.application.semantic_task_contracts import AgentContract
from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
    IntegratedSemanticWorker,
    ProviderExecutionReadiness,
    ProviderExecutionState,
    SemanticWorkerConfig,
    SemanticWorkerIdentity,
)
from memoriesql.infrastructure.models import pydanticai_executor as executor_module
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
    LeafAgentSpec,
    ManagedModelAdmission,
    ModelProfileBinding,
    PydanticAIAgentRegistry,
    PydanticAIModelProfileRegistry,
    PydanticAISemanticExecutor,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
    PostgresRelationDeliveryRecorder,
)

if TYPE_CHECKING:
    from tests.runtime import test_authored_relations as relation_fixtures
    from tests.runtime.test_managed_dispatch import FictionalManagedModel, units
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_authored_relations as relation_fixtures
    from test_managed_dispatch import FictionalManagedModel, units
    from test_postgres_runtime import migrate

VOCABULARY = relation_fixtures.VOCABULARY
NEW_TABLES = (
    "relation_assessment_dispatch_policies", "relation_assessments",
    "relation_assessment_deliveries", "assessed_relations", "assessed_relation_statements",
    "relation_pair_dispositions", "relation_retirements",
)
Packet = dict[str, Any]


class RelationAssessment(relation_fixtures.AuthoredRelations):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=27, target_version=29)
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,p.queue_name FROM memoriesql.semantic_worker_claim_policies c CROSS JOIN memoriesql.semantic_task_admission_policies p WHERE p.task_kind='memory.semantic.assess-relations' AND p.contract_revision=1 AND c.principal_id=%s AND c.task_kind='memory.semantic.author-complete-unit' AND c.contract_revision=6",
            (self.worker_principal,),
        )
        self.relation_policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.relation_assessment_dispatch_policies VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'active')",
            (self.tenant, self.relation_policy, self.workspace, self.attestor, self.principal,
             "c" * 64, self.now, self.now + timedelta(hours=1)),
        )
        self.assessments = PostgresRelationAssessments(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        self.author_plan: Callable[[Packet], dict[str, Any]] | None = None
        self.specialist_plan: Callable[[Packet], dict[str, Any]] | None = None
        self.frames: list[Packet] = []

    # -- fixture helpers -------------------------------------------------

    def bead(self, *texts: str, key: str, source: uuid.UUID | None = None) -> uuid.UUID:
        """An accepted fictional bead with one statement per text.

        Its one unit retains every text as source, so each statement's facts and
        qualifications are in the evidence delivered for it.
        """

        def statements(extras: dict[str, Any], bead: dict[str, Any], _: list[Any]) -> None:
            bead["statements"][0]["statement_text"] = texts[0]
            bead["render"]["title"]["text"] = texts[0][:240]
            bead["render"]["summary"][0]["text"] = texts[0]
            for text in texts[1:]:
                extra = dict(bead["statements"][0], statement_id=str(uuid.uuid4()),
                             statement_text=text)
                bead["statements"].append(extra)
                bead["render"]["summary"].append(
                    {"text": text, "statement_ids": [extra["statement_id"]]}
                )

        return self.author("\n".join(texts), key, plan=statements, source=source)

    @staticmethod
    def delivered(packet: Packet) -> str:
        """The retained source a packet delivered: every excerpt's text or parts, in order."""
        return "\n".join(
            excerpt["content"] if excerpt["content"] is not None
            else "".join(part["content"] for part in excerpt["parts"])
            for excerpt in packet["evidence"]
        )

    def remote_scope(self) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        """An explicit scope with its own fictional source that the owner, the worker and
        the attestor may each read. Returns the scope, its source and the owner's grant."""
        scope, owner_grant, source = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        pairings = self.db.execute(
            "SELECT r.pairing_grant_id, r.revision, r.allowed_capabilities, r.allowed_access_scope_ids "
            "FROM memoriesql.pairing_grant_revisions AS r "
            "JOIN memoriesql.pairing_grants AS g USING (tenant_id, pairing_grant_id) "
            "WHERE g.paired_principal_id IN (%s,%s) AND r.revision = (SELECT max(l.revision) "
            "FROM memoriesql.pairing_grant_revisions AS l "
            "WHERE l.tenant_id = r.tenant_id AND l.pairing_grant_id = r.pairing_grant_id)",
            (self.worker_principal, self.attestor),
        ).fetchall()
        self.assertEqual(len(pairings), 2)
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.create_access_scope(%s,%s,'explicit','Fictional remote orchard',%s)",
                (scope, uuid.uuid4(), self.now),
            )
            for principal, grant in ((self.principal, owner_grant), (self.worker_principal, uuid.uuid4()),
                                     (self.attestor, uuid.uuid4())):
                self.db.execute(
                    "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                    (grant, principal, scope, ["read", "write"], self.now, self.now + timedelta(hours=1)),
                )
            # The worker's and the attestor's pairings extend to the new scope.
            for pairing, revision, capabilities, scopes in pairings:
                self.db.execute(
                    "SELECT memoriesql.revise_pairing_grant(%s,%s,%s,%s,'active',%s,%s,%s)",
                    (pairing, revision, capabilities, [*scopes, scope], self.now,
                     self.now + timedelta(hours=1), self.now),
                )
        self.db.execute(
            "INSERT INTO memoriesql.protected_resources SELECT tenant_id,workspace_id,%s,%s,resource_kind,owner_user_id,status,created_by_principal_id,created_at FROM memoriesql.protected_resources WHERE resource_id=%s",
            (scope, source, self.source),
        )
        self.db.execute(
            "INSERT INTO memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,source_object_id,source_system,object_kind,external_object_id,schema_version,metadata,created_at,last_observed_at,owner_user_id) SELECT tenant_id,workspace_id,%s,%s,source_system,object_kind,'orchard.remote',schema_version,metadata,created_at,last_observed_at,owner_user_id FROM memoriesql.source_objects WHERE source_object_id=%s",
            (scope, source, self.source),
        )
        producer, policy = uuid.uuid4(), uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies SELECT tenant_id,workspace_id,%s,%s,%s,producer_principal_id,qualification_ref,normalization_policy_version,qualification_evidence_sha256,approved_by_principal_id,created_at,expires_at,status FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
            (scope, producer, source, self.policy),
        )
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,workspace_id,%s,%s,attestor_principal_id,approved_by_principal_id,qualification_evidence_sha256,created_at,expires_at,status,6 FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (policy, scope, source, self.dispatch_policy),
        )
        self.producers[source], self.policies[source] = producer, policy
        self.checkpoints[source], self.positions[source] = "orchard.remote.raw", (0, 0)
        return scope, source, owner_grant

    def remote_bead(self, *texts: str, key: str, scope: uuid.UUID, source: uuid.UUID) -> uuid.UUID:
        local, self.scope = self.scope, scope
        try:
            return self.bead(*texts, key=key, source=source)
        finally:
            self.scope = local

    def revoke(self, grant: uuid.UUID) -> None:
        """Revoke one of the owner's grants; the worker's and the attestor's stay active."""
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,1,%s,'revoked',%s,%s,%s)",
                (grant, ["read", "write"], self.now, self.now + timedelta(hours=1), self.now),
            )

    def readers(self, scope: uuid.UUID) -> dict[str, bool]:
        """Whether the owner, the worker and the attestor may each read a scope now."""
        readers = {}
        for name, secret, role in (
            ("owner", self.secret_hash, "SET LOCAL ROLE memoriesql_application"),
            ("worker", self.worker_secret, "SET LOCAL ROLE memoriesql_worker"),
            ("attestor", self.attestor_secret, "SET LOCAL ROLE memoriesql_worker"),
        ):
            with self.connection() as connection, connection.transaction():
                connection.execute(role)
                PostgresAuthorizationPort(connection).begin_context(
                    credential_sha256=secret, requested_workspace_id=self.workspace)
                readers[name] = connection.execute(
                    "SELECT memoriesql.current_context_scope_permits(%s,'read')", (scope,)).fetchone()[0]
        return readers

    def origin_refusal(self, task: uuid.UUID) -> str | None:
        """The refusal the worker's own pin check now raises for a task, if any."""
        with self.connection() as connection, connection.transaction():
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=self.worker_secret, requested_workspace_id=self.workspace)
            try:
                with connection.transaction():
                    connection.execute(
                        "SELECT memoriesql.relation_assessment_pins_authorize(%s,%s)", (self.tenant, task))
            except psycopg.errors.InsufficientPrivilege as error:
                return error.diag.message_primary
        return None

    def revoking(self, grant: uuid.UUID, *, after: str) -> Any:
        """The real attestor, then the owner's grant revoked once one role's delivery is recorded."""
        attestor = PostgresRelationDeliveryRecorder(
            connection_factory=self.connection, credential_sha256=self.attestor_secret,
            workspace_id=self.workspace)
        test = self

        class Revoking:
            async def record_relation_delivery(self, **kwargs: Any) -> None:
                await attestor.record_relation_delivery(**kwargs)
                if ("author" if kwargs["decision"] is None else "specialist") == after:
                    test.revoke(grant)

        return Revoking()

    def beads_as_they_were(self) -> list[tuple[Any, ...]]:
        """Every accepted and stored bead version, to show paused work changed none."""
        return self.db.execute(
            "SELECT 'accepted', bead_id::text, bead_version_id::text FROM memoriesql.accepted_bead_semantics "
            "UNION ALL SELECT 'stored', bead_id::text, bead_version_id::text FROM memoriesql.bead_versions "
            "ORDER BY 1, 2, 3"
        ).fetchall()

    def statements(self, bead: uuid.UUID) -> list[str]:
        return [
            str(row[0])
            for row in self.db.execute(
                "SELECT statement_id FROM memoriesql.bead_semantic_statements WHERE bead_id=%s ORDER BY statement_sequence",
                (bead,),
            ).fetchall()
        ]

    def activate_relations(
        self,
        subject: uuid.UUID,
        candidates: tuple[uuid.UUID, ...] = (),
        *,
        key: str = "orchard.assess",
        reconsiders: uuid.UUID | None = None,
        vocabulary: tuple[RelationTypePin, ...] = VOCABULARY,
    ) -> Any:
        return self.assessments.activate(
            ActivateRelationAssessment(
                idempotency_key=key,
                subject_bead_id=subject,
                candidate_bead_ids=candidates,
                relation_vocabulary=vocabulary,
                dispatch_policy_id=self.relation_policy,
                reconsiders_task_id=reconsiders,
            )
        )

    def relation_response(self, messages: Any, info: Any) -> ModelResponse:
        prompts = [
            p.content
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        self.assertEqual(len(prompts), 1)
        frame = json.loads(cast(str, prompts[0]))
        packet = frame["relation_packet"]
        self.frames.append(packet)
        if packet["role"] == "author":
            output = (self.author_plan or self.no_relations)(packet)
            data: dict[str, Any] = {"action": "finish", "notes": "", "typed_output": output}
        else:
            data = (self.specialist_plan or self.agree)(packet)
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=data)])

    def relation_worker(self, *, recorder: Any = True) -> IntegratedSemanticWorker:
        task = RELATION_ASSESSMENT_TASK
        modules = BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
        registry = load_relation_assessment_task_registry(modules)
        agents = PydanticAIAgentRegistry(
            leaf_specs=(
                LeafAgentSpec(
                    agent_key=cast(str, task.leaf_agent_key),
                    input_contract=task.input_contract.reference,
                    output_contract=task.output_contract.reference,
                    input_model_type=task.input_contract.model_type,
                    output_model_type=task.output_contract.model_type,
                    instructions="Use only fictional evidence; preserve uncertainty.",
                    model_profiles=(task.model_profile,),
                ),
                LeafAgentSpec(
                    agent_key=SPECIALIST_KEY,
                    input_contract=SPECIALIST_INPUT.reference,
                    output_contract=SPECIALIST_OUTPUT.reference,
                    input_model_type=RelationSpecialistPacket,
                    output_model_type=RelationSpecialistDecision,
                    instructions="Assess each fictional proposal exactly; abstain when unsure.",
                    model_profiles=(SPECIALIST_PROFILE,),
                ),
            ),
            conductors=(),
            agent_contracts=(
                AgentContract(
                    agent_key=cast(str, task.leaf_agent_key),
                    input_contract=task.input_contract.reference,
                    output_contract=task.output_contract.reference,
                    maximum_effort_key="standard",
                ),
                SPECIALIST_AGENT,
            ),
        )
        profiles = PydanticAIModelProfileRegistry(
            tuple(
                ModelProfileBinding(
                    reference=reference,
                    model=FunctionModel(self.relation_response),
                    request_target=CHARACTERIZED_TEST_REQUEST_TARGET,
                )
                for reference in (task.model_profile, SPECIALIST_PROFILE)
            )
        )
        executor = PydanticAISemanticExecutor(
            semantic_registry=registry,
            composition_provider=lambda: modules.compose(("core",)),
            agent_registry=agents,
            model_profiles=profiles,
            run_id_factory=lambda role: f"orchard.relation.{role}.{uuid.uuid4().hex[:8]}",
        )
        return IntegratedSemanticWorker(
            connection_factory=self.connection,
            identity=SemanticWorkerIdentity(
                credential_sha256=self.worker_secret,
                workspace_id=self.workspace,
                worker_id="orchard.worker",
                worker_instance_id="orchard.instance",
            ),
            semantic_registry=registry,
            composition_provider=lambda: modules.compose(("core",)),
            executor=cast(Any, executor),
            readiness_provider=lambda: ProviderExecutionReadiness(
                ProviderExecutionState.CONFIGURED, "fictional.configured"
            ),
            config=SemanticWorkerConfig(heartbeat_interval_seconds=10),
            exposure_recorder=(
                PostgresRelationDeliveryRecorder(
                    connection_factory=self.connection,
                    credential_sha256=self.attestor_secret,
                    workspace_id=self.workspace,
                )
                if recorder is True
                else recorder
            ),
        )

    def run_relations(self, expect: str | None = "succeeded") -> Any:
        self.frames = []
        receipt = asyncio.run(self.relation_worker().run_once())
        if expect is not None:
            self.assertEqual(receipt.task_status, expect, receipt)
        return receipt

    # -- fictional author and specialist outcomes -------------------------

    @staticmethod
    def pairs(packet: Packet) -> list[tuple[str, str]]:
        ids = sorted(b["bead_id"] for b in packet["beads"])
        return [(a, b) for i, a in enumerate(ids) for b in ids[i:]]

    def dispositions(
        self, packet: Packet, proposals: list[dict[str, Any]], default: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        related = {
            tuple(sorted((p["source"]["bead_id"], p["target"]["bead_id"]))) for p in proposals
        }
        rows = []
        for first, second in self.pairs(packet):
            if (first, second) in related:
                rows.append({"first_bead_id": first, "second_bead_id": second,
                             "disposition": "related", "abstention": None, "reason": None})
            else:
                rows.append({"first_bead_id": first, "second_bead_id": second,
                             **(default or {"disposition": "not_related", "abstention": None,
                                            "reason": None})})
        return rows

    def no_relations(self, packet: Packet) -> dict[str, Any]:
        return {"proposals": [], "dispositions": self.dispositions(packet, [])}

    @staticmethod
    def pinned(packet: Packet, bead: uuid.UUID) -> dict[str, Any]:
        return next(b for b in packet["beads"] if b["bead_id"] == str(bead))

    def endpoint(self, packet: Packet, bead: uuid.UUID, *indexes: int) -> dict[str, Any]:
        pinned = self.pinned(packet, bead)
        return {"bead_id": pinned["bead_id"], "bead_version_id": pinned["bead_version_id"],
                "statement_ids": [pinned["statements"][i]["statement_id"] for i in indexes]}

    def basis_pin(self, packet: Packet, bead: uuid.UUID, index: int) -> dict[str, Any]:
        pinned = self.pinned(packet, bead)
        return {"bead_id": pinned["bead_id"], "bead_version_id": pinned["bead_version_id"],
                "statement_id": pinned["statements"][index]["statement_id"]}

    def proposal(
        self,
        packet: Packet,
        type_key: str,
        source: dict[str, Any],
        target: dict[str, Any],
        *,
        basis_statements: tuple[dict[str, Any], ...] = (),
        basis: str = "source_stated",
        qualification: str | None = None,
        retires: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        named = [*source["statement_ids"], *target["statement_ids"],
                 *(b["statement_id"] for b in basis_statements)]
        by_id = {s["statement_id"]: s for b in packet["beads"] for s in b["statements"]}
        evidence = [
            {"statement_id": sid, "source_unit_id": by_id[sid]["evidence"][0]["source_unit_id"],
             "content_hash": by_id[sid]["evidence"][0]["content_hash"]}
            for sid in named[:8]
        ]
        return {
            "proposal_id": str(uuid.uuid4()), "relation_type": {"key": type_key, "revision": 1},
            "source": source, "target": target, "basis_statements": list(basis_statements),
            "evidence": evidence, "basis": basis, "qualification": qualification,
            "rationale": ("The fictional note states this connection." if basis == "source_stated"
                          else "The fictional author infers this; the note does not state it."),
            "author_confidence": 0.8, "retires": retires,
        }

    @staticmethod
    def agree(packet: Packet) -> dict[str, Any]:
        return {"contract_version": 1, "judgments": [
            {"proposal_id": p["proposal_id"], "outcome": "assessed", "consistent": True,
             "warranted": [{"relation_type": p["relation_type"], "direction": "as_proposed"}],
             "abstention": None,
             "rationale": ("The fictional evidence states it." if p["basis"] == "source_stated"
                           else "The fictional evidence supports the inference.")}
            for p in packet["proposals"]]}

    def propose(self, build: Callable[[Packet], list[dict[str, Any]]]) -> None:
        def author(packet: Packet) -> dict[str, Any]:
            proposals = build(packet)
            return {"proposals": proposals, "dispositions": self.dispositions(packet, proposals)}

        self.author_plan = author

    # -- reads -----------------------------------------------------------

    def inspect_v2(self, bead: uuid.UUID) -> Any:
        return self.assessments.inspect_relations(InspectBeadRelationsV2(bead_id=bead))

    # -- tests -----------------------------------------------------------

    def test_same_bead_endpoints_with_a_separate_basis_are_accepted_on_agreement(self) -> None:
        # A report isolates a change as the cause of a regression; the trial
        # result is the separate basis, with its stated conditions as the qualification.
        report = self.bead(
            "Fictional change: the irrigation timer moved to dawn.",
            "Fictional regression: the east rows dried out.",
            "Fictional trial: in two dry weeks, with the timer restored the rows recovered, twice.",
            key="report",
        )
        change, regression, trial = self.statements(report)
        self.activate_relations(report)
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, report, 1), self.endpoint(packet, report, 0),
            basis_statements=(self.basis_pin(packet, report, 2),),
            qualification="In the fictional trial's two dry weeks.",
        )])
        self.run_relations()
        roles = [p["role"] for p in self.frames]
        self.assertEqual(roles, ["author", "specialist"])
        # Both runs received the same retained source, not only statement texts, and it
        # holds the change, the regression, the trial and the trial's conditions.
        self.assertEqual(self.frames[0]["evidence"], self.frames[1]["evidence"])
        self.assertTrue(self.frames[0]["evidence"][0]["parts"])
        for packet in self.frames:
            for fact in ("the irrigation timer moved to dawn", "the east rows dried out",
                         "in two dry weeks", "with the timer restored the rows recovered, twice"):
                self.assertIn(fact, self.delivered(packet), packet["role"])
        inspection = self.inspect_v2(report)
        self.assertEqual(inspection.outcome, "available", inspection)
        (relation,) = inspection.relations
        self.assertEqual(
            (relation.kind, relation.state, relation.direction, relation.relation_type.key),
            ("assessed", "active", "internal", "caused_by"),
        )
        self.assertEqual([str(s.statement_id) for s in relation.source_statements], [regression])
        self.assertEqual([str(s.statement_id) for s in relation.target_statements], [change])
        self.assertEqual([str(s.statement_id) for s in relation.basis_statements], [trial])
        self.assertEqual(relation.qualification, "In the fictional trial's two dry weeks.")
        assert relation.judgment is not None
        self.assertTrue(relation.judgment.consistent)
        (task,) = inspection.relation_tasks
        self.assertEqual(task.status, "succeeded")
        (pair,) = inspection.pair_dispositions
        self.assertEqual((pair.first_bead_id, pair.disposition), (report, "related"))
        self.assertIn("caused_by", {t.key for t in inspection.relation_types})

    def test_foreign_endpoints_carry_the_reporter_as_an_attributed_basis(self) -> None:
        # Sam reports a suspicion that D caused O; D and O are earlier beads.
        deploy = self.bead("Fictional deploy D went out at noon.", key="deploy")
        outage = self.bead("Fictional outage O began at one.", key="outage")
        report = self.bead("Fictional Sam suspects deploy D caused outage O.", key="sam")
        self.activate_relations(report, (deploy, outage))
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, outage, 0), self.endpoint(packet, deploy, 0),
            basis_statements=(self.basis_pin(packet, report, 0),),
            qualification="Sam's suspicion, not a confirmed cause.",
        )])
        self.run_relations()
        # Both runs received each bead's retained source, including Sam's report.
        self.assertEqual([p["role"] for p in self.frames], ["author", "specialist"])
        for packet in self.frames:
            for fact in ("deploy D went out at noon", "outage O began at one",
                         "Sam suspects deploy D caused outage O"):
                self.assertIn(fact, self.delivered(packet), packet["role"])
        (relation,) = self.inspect_v2(report).relations
        self.assertEqual(
            (relation.direction, relation.source_bead_id, relation.target_bead_id),
            ("basis", outage, deploy),
        )
        (incoming,) = self.inspect_v2(deploy).relations
        self.assertEqual((incoming.relation_id, incoming.direction), (relation.relation_id, "incoming"))
        # Each read shows the pairs its bead belongs to; the report's three pairs
        # stay not related, and the deploy-outage pair is related.
        self.assertEqual(
            sorted(d.disposition for d in self.inspect_v2(report).pair_dispositions),
            ["not_related"] * 3,
        )
        dispositions = {(d.first_bead_id, d.second_bead_id): d.disposition
                        for d in self.inspect_v2(deploy).pair_dispositions}
        self.assertEqual(dispositions[cast(Any, tuple(sorted((deploy, outage), key=str)))], "related")

    def test_abstentions_record_every_pair_without_a_specialist_call(self) -> None:
        # Nothing states a connection, and similar wording is no fit. Within the
        # failure note, which failure came first is not recorded, so any relation
        # between its statements is ambiguous.
        first = self.bead("Fictional log: deploy at noon.", key="log-a")
        second = self.bead("Fictional log: errors at one.", key="log-b")
        failures = self.bead(
            "Fictional pump U failed.", "Fictional valve V failed.",
            "Fictional log: U and V failed together; which failed first is not recorded.",
            key="failures",
        )
        self.activate_relations(first, (second, failures))

        def author(packet: Packet) -> dict[str, Any]:
            rows = self.dispositions(
                packet, [], {"disposition": "abstained", "abstention": "no_fit", "reason": None})
            for row in rows:
                if row["first_bead_id"] == row["second_bead_id"] == str(failures):
                    row["abstention"] = "ambiguous"
            return {"proposals": [], "dispositions": rows}

        self.author_plan = author
        self.run_relations()
        self.assertEqual([p["role"] for p in self.frames], ["author"])
        self.assertIn("which failed first is not recorded", self.delivered(self.frames[0]))
        inspection = self.inspect_v2(first)
        self.assertEqual(inspection.relations, ())
        self.assertEqual(
            {(d.disposition, d.abstention) for d in inspection.pair_dispositions},
            {("abstained", "no_fit")},
        )
        self.assertEqual(len(inspection.pair_dispositions), 3)  # with second, with failures, with itself
        abstentions = {(d.first_bead_id, d.second_bead_id): d.abstention
                       for d in self.inspect_v2(failures).pair_dispositions}
        self.assertEqual(abstentions[(failures, failures)], "ambiguous")

    def test_a_qualifier_in_the_packet_is_used_and_an_absent_one_is_an_abstention(self) -> None:
        # First variant: the operator's condition is retained source in the
        # authorized packet, so the author uses it; a universal abstention would
        # fail this variant.
        valve = self.bead(
            "Fictional valve V closed.",
            "Fictional pressure P dropped.",
            "Fictional operator: only while pump K runs does closing V drop P.",
            key="valve",
        )
        self.activate_relations(valve)
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, valve, 1), self.endpoint(packet, valve, 0),
            basis_statements=(self.basis_pin(packet, valve, 2),),
            qualification="Only while pump K runs.",
        )])
        self.run_relations()
        self.assertEqual([p["role"] for p in self.frames], ["author", "specialist"])
        for packet in self.frames:
            for fact in ("valve V closed", "pressure P dropped",
                         "only while pump K runs does closing V drop P"):
                self.assertIn(fact, self.delivered(packet), packet["role"])
        (relation,) = self.inspect_v2(valve).relations
        self.assertEqual((relation.state, relation.qualification), ("active", "Only while pump K runs."))
        # Second variant: the condition that links gauge and vent is recorded only in
        # a scope the activator can no longer read, so no task can pin it. The packet
        # lacks it; the author abstains for insufficient evidence and asserts nothing.
        scope, source, owner_grant = self.remote_scope()
        hidden = self.remote_bead("Fictional operator: only while fan Q runs does opening W drop G.",
                                  key="condition", scope=scope, source=source)
        self.revoke(owner_grant)
        gauge = self.bead("Fictional gauge G fell.", "Fictional vent W opened.", key="gauge")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.activate_relations(gauge, (hidden,), key="orchard.assess.gauge.hidden")
        self.activate_relations(gauge, key="orchard.assess.gauge")
        self.author_plan = lambda packet: {"proposals": [], "dispositions": self.dispositions(
            packet, [], {"disposition": "abstained", "abstention": "insufficient_evidence",
                         "reason": "The condition that links them is not in the packet."})}
        self.run_relations()
        self.assertEqual([p["role"] for p in self.frames], ["author"])
        delivered = self.delivered(self.frames[0])
        self.assertIn("gauge G fell", delivered)
        self.assertIn("vent W opened", delivered)
        self.assertNotIn("only while fan Q runs", delivered)
        inspection = self.inspect_v2(gauge)
        self.assertEqual(inspection.relations, ())
        (pair,) = inspection.pair_dispositions
        self.assertEqual((pair.disposition, pair.abstention), ("abstained", "insufficient_evidence"))

    def test_disagreement_stays_unaccepted_and_agreed_predicates_stand_together(self) -> None:
        # Two warranted predicates over one pair are both accepted;
        # the same predicate over different endpoints is a disagreement.
        grant = self.bead(
            "Fictional permission R was granted.",
            "Fictional migration M can now run.",
            "Fictional migration M has not run.",
            key="grant",
        )
        self.activate_relations(grant)
        created: dict[str, str] = {}

        def build(packet: Packet) -> list[dict[str, Any]]:
            enables = self.proposal(packet, "enables", self.endpoint(packet, grant, 0),
                                    self.endpoint(packet, grant, 1), basis="agent_inferred")
            depends = self.proposal(packet, "depends_on", self.endpoint(packet, grant, 1),
                                    self.endpoint(packet, grant, 0), basis="agent_inferred")
            wrong = self.proposal(packet, "enables", self.endpoint(packet, grant, 0),
                                  self.endpoint(packet, grant, 2), basis="agent_inferred")
            created.update(enables=enables["proposal_id"], depends=depends["proposal_id"],
                           wrong=wrong["proposal_id"])
            return [enables, depends, wrong]

        def judge(packet: Packet) -> dict[str, Any]:
            decision = self.agree(packet)
            for judgment in decision["judgments"]:
                if judgment["proposal_id"] == created["wrong"]:
                    # Same predicate, but the source does not state what relates to what.
                    judgment.update(consistent=False, rationale="Permission R enables M, not its not having run.")
            return decision

        self.propose(build)
        self.specialist_plan = judge
        self.run_relations()
        states = {str(r.relation_id): r.state for r in self.inspect_v2(grant).relations}
        self.assertEqual(states, {created["enables"]: "active", created["depends"]: "active",
                                  created["wrong"]: "not_accepted"})
        wrong = next(r for r in self.inspect_v2(grant).relations if str(r.relation_id) == created["wrong"])
        assert wrong.judgment is not None
        self.assertEqual((wrong.judgment.outcome, wrong.judgment.consistent), ("assessed", False))
        self.assertIsNotNone(wrong.specialist_run_ref)
        self.assertNotEqual(wrong.specialist_run_ref, wrong.author_run_ref)

    def test_a_proposal_changed_after_its_judgment_is_refused(self) -> None:
        # The specialist judges a copy whose qualification differs from the output's.
        note = self.bead("Fictional flue F blocked in the storm.",
                         "Fictional stove S smoked during the storm.", key="flue")
        self.activate_relations(note)
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, note, 1), self.endpoint(packet, note, 0),
            basis="agent_inferred", qualification="Only during the fictional storm.")])
        class Substituted(RelationSpecialistPacket):
            def __init__(self, **fields: Any) -> None:
                fields["proposals"] = tuple(
                    dict(p if isinstance(p, dict) else p.model_dump(mode="json"),
                         qualification="At all times.")
                    for p in fields["proposals"])
                super().__init__(**fields)

        with patch.object(executor_module, "RelationSpecialistPacket", Substituted):
            self.run_relations(expect="failed_terminal")
        self.assertEqual(self.frames[1]["proposals"][0]["qualification"], "At all times.")
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))

    def test_an_abstaining_specialist_leaves_the_proposal_unaccepted(self) -> None:
        note = self.bead("Fictional audit A passed.", "Fictional release L shipped.", key="audit")
        self.activate_relations(note)
        self.propose(lambda packet: [self.proposal(
            packet, "enables", self.endpoint(packet, note, 0), self.endpoint(packet, note, 1),
            basis="agent_inferred")])
        self.specialist_plan = lambda packet: {"contract_version": 1, "judgments": [
            {"proposal_id": p["proposal_id"], "outcome": "abstained", "consistent": None, "warranted": [],
             "abstention": "ambiguous", "rationale": "The fictional note does not say which came first."}
            for p in packet["proposals"]]}
        self.run_relations()
        (relation,) = self.inspect_v2(note).relations
        self.assertEqual(relation.state, "not_accepted")
        assert relation.judgment is not None
        self.assertEqual(relation.judgment.abstention, "ambiguous")

    def test_an_endpoint_revoked_before_apply_pauses_the_task_and_changes_nothing(self) -> None:
        # Authority over every endpoint is rechecked at apply, after both
        # deliveries were attested.
        other = self.add_source("revoked")
        remote = self.bead("Fictional remote pump failed.", key="remote", source=other)
        local = self.bead("Fictional local rows wilted.", key="local")
        self.activate_relations(local, (remote,))
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, local, 0), self.endpoint(packet, remote, 0),
            basis="agent_inferred")])
        attestor = PostgresRelationDeliveryRecorder(
            connection_factory=self.connection, credential_sha256=self.attestor_secret,
            workspace_id=self.workspace)
        test = self

        class RevokeAfterSpecialist:
            async def record_relation_delivery(self, **kwargs: Any) -> None:
                await attestor.record_relation_delivery(**kwargs)
                if kwargs["decision"] is not None:
                    test.db.execute(
                        "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
                        (other,),
                    )

        self.frames = []
        receipt = asyncio.run(self.relation_worker(recorder=RevokeAfterSpecialist()).run_once())
        self.assertNotEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual([p["role"] for p in self.frames], ["author", "specialist"])
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.relation_assessment_deliveries"), (2,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.relation_pair_dispositions"), (0,))
        self.assertEqual(
            self.row("SELECT status FROM memoriesql.semantic_tasks WHERE task_kind='memory.semantic.assess-relations'"),
            ("policy_paused",),
        )

    def test_an_origin_losing_a_candidate_scope_pauses_before_any_dispatch(self) -> None:
        # The worker and the attestor keep their own authority over the candidate's
        # scope; only the activating origin loses it. Nothing is hydrated or sent.
        scope, source, owner_grant = self.remote_scope()
        remote = self.remote_bead("Fictional remote pump failed.", key="remote", scope=scope, source=source)
        local = self.bead("Fictional local rows wilted.", key="local")
        receipt = self.activate_relations(local, (remote,))
        before = self.beads_as_they_were()
        self.revoke(owner_grant)
        self.assertEqual(self.readers(scope), {"owner": False, "worker": True, "attestor": True})
        self.run_relations(expect=None)
        self.assertEqual(self.frames, [])
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents WHERE task_kind='memory.semantic.assess-relations'"), (0,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.relation_assessment_deliveries"), (0,))
        self.assertEqual(self.origin_refusal(receipt.task_id), "relation_assessment_origin_unavailable")
        self.assertEqual(self.row("SELECT status FROM memoriesql.semantic_tasks WHERE task_id=%s", (receipt.task_id,)),
                         ("policy_paused",))
        # Paused relation work leaves every accepted bead as it was.
        self.assertEqual(self.beads_as_they_were(), before)

    def test_an_origin_losing_a_candidate_scope_after_the_author_stops_the_specialist(self) -> None:
        scope, source, owner_grant = self.remote_scope()
        remote = self.remote_bead("Fictional remote pump failed.", key="remote", scope=scope, source=source)
        local = self.bead("Fictional local rows wilted.", key="local")
        receipt = self.activate_relations(local, (remote,))
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, local, 0), self.endpoint(packet, remote, 0),
            basis="agent_inferred")])
        before = self.beads_as_they_were()
        self.frames = []
        asyncio.run(self.relation_worker(recorder=self.revoking(owner_grant, after="author")).run_once())
        # The specialist is never dispatched once the origin loses the candidate.
        self.assertEqual([p["role"] for p in self.frames], ["author"])
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.relation_assessment_deliveries"), (1,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))
        self.assertEqual(self.origin_refusal(receipt.task_id), "relation_assessment_origin_unavailable")
        self.assertEqual(self.row("SELECT status FROM memoriesql.semantic_tasks WHERE task_id=%s", (receipt.task_id,)),
                         ("policy_paused",))
        self.assertEqual(self.beads_as_they_were(), before)

    def test_an_origin_losing_a_basis_only_scope_pauses_at_apply(self) -> None:
        # The remote bead is only the attributed basis; both endpoints stay local.
        # Both deliveries were authorized and attested before the origin lost the
        # remote scope, and apply refuses the whole outcome.
        scope, source, owner_grant = self.remote_scope()
        report = self.remote_bead("Fictional inspector: the dry well wilted the local rows.",
                                  key="inspector", scope=scope, source=source)
        local = self.bead("Fictional local well ran dry.", "Fictional local rows wilted.", key="local")
        receipt = self.activate_relations(local, (report,))
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, local, 1), self.endpoint(packet, local, 0),
            basis_statements=(self.basis_pin(packet, report, 0),))])
        before = self.beads_as_they_were()
        self.frames = []
        asyncio.run(self.relation_worker(recorder=self.revoking(owner_grant, after="specialist")).run_once())
        self.assertEqual([p["role"] for p in self.frames], ["author", "specialist"])
        for packet in self.frames:
            self.assertIn("the dry well wilted the local rows", self.delivered(packet), packet["role"])
        self.assertEqual(self.readers(scope), {"owner": False, "worker": True, "attestor": True})
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.relation_assessment_deliveries"), (2,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.relation_pair_dispositions"), (0,))
        self.assertEqual(self.origin_refusal(receipt.task_id), "relation_assessment_origin_unavailable")
        self.assertEqual(self.row("SELECT status FROM memoriesql.semantic_tasks WHERE task_id=%s", (receipt.task_id,)),
                         ("policy_paused",))
        self.assertEqual(self.beads_as_they_were(), before)

    def test_an_incomplete_specialist_batch_is_refused_and_the_bead_is_untouched(self) -> None:
        note = self.bead("Fictional door D opened.", "Fictional hall H cooled.", key="door")
        before = self.row("SELECT count(*), max(bead_version_id::text) FROM memoriesql.bead_versions")
        self.activate_relations(note)
        self.propose(lambda packet: [
            self.proposal(packet, "caused_by", self.endpoint(packet, note, 1), self.endpoint(packet, note, 0),
                          basis="agent_inferred"),
            self.proposal(packet, "led_to", self.endpoint(packet, note, 0), self.endpoint(packet, note, 1),
                          basis="agent_inferred"),
        ])
        # One proposal is never judged; nothing is inferred for it.
        self.specialist_plan = lambda packet: {"contract_version": 1,
                                               "judgments": self.agree(packet)["judgments"][:1]}
        self.run_relations(expect="failed_terminal")
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))
        # A failed relation task never touches the accepted bead or its receipts,
        # and keeps its own visible status.
        self.assertEqual(
            self.row("SELECT count(*), max(bead_version_id::text) FROM memoriesql.bead_versions"), before
        )
        self.assertEqual({t.status for t in self.inspect_v2(note).relation_tasks}, {"failed_terminal"})

    def test_an_oversized_specialist_batch_is_refused_never_truncated(self) -> None:
        note = self.bead("Fictional kiln K fired.", "Fictional glaze G cracked.", key="kiln")
        self.activate_relations(note)
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, note, 1), self.endpoint(packet, note, 0),
            basis="agent_inferred")])
        original = ra._packet_bounds

        def author_fits_but_batch_does_not(packet: Any, evidence: Any) -> None:
            if isinstance(packet, RelationSpecialistPacket):
                raise ValueError("relation packet exceeds its bound; never truncated")
            original(packet, evidence)

        with patch.object(ra, "_packet_bounds", author_fits_but_batch_does_not):
            receipt = self.run_relations(expect=None)
        self.assertNotEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual([p["role"] for p in self.frames], ["author"])
        self.assertEqual(
            self.row("SELECT error_code FROM memoriesql.semantic_task_attempts WHERE error_code IS NOT NULL"),
            ("relation_assessment.specialist_batch_oversized",),
        )
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))

    def test_same_write_direction_correction_retires_and_reverses_without_a_cycle(self) -> None:
        source = self.bead("Fictional source ledger: four crates.", key="ledger")
        copy_ = self.bead("Fictional copy: four crates.", key="copy")
        first: dict[str, str] = {}

        def forward(packet: Packet) -> list[dict[str, Any]]:
            # The wrong direction: the ledger is said to derive from its copy.
            proposal = self.proposal(packet, "derived_from", self.endpoint(packet, source, 0),
                                     self.endpoint(packet, copy_, 0), basis="agent_inferred")
            first["id"] = proposal["proposal_id"]
            return [proposal]

        self.activate_relations(source, (copy_,))
        self.propose(forward)
        self.run_relations()
        second: dict[str, str] = {}

        def reverse(packet: Packet) -> list[dict[str, Any]]:
            proposal = self.proposal(
                packet, "derived_from", self.endpoint(packet, copy_, 0), self.endpoint(packet, source, 0),
                basis="agent_inferred",
                retires={"relation_kind": "assessed", "relation_id": first["id"],
                         "reason": "The copy derives from the ledger, not the reverse."},
            )
            second["id"] = proposal["proposal_id"]
            return [proposal]

        self.activate_relations(copy_, (source,), key="orchard.assess.reverse")
        self.propose(reverse)
        self.run_relations()
        relations = {str(r.relation_id): r for r in self.inspect_v2(source).relations}
        retired, replacement = relations[first["id"]], relations[second["id"]]
        self.assertEqual((retired.state, replacement.state), ("superseded", "active"))
        self.assertEqual([str(i) for i in retired.superseded_by], [second["id"]])
        self.assertEqual([(e.action, e.origin) for e in retired.events], [("retire", "authored")])

    def test_one_cycle_check_covers_revision_six_and_assessed_assertions(self) -> None:
        ledger = self.author("Fictional ledger: four crates.", "ledger")
        created: dict[str, str] = {}
        summary = self.author(
            "Fictional summary: four crates.", "summary", candidates=(ledger,),
            plan=lambda extras, bead, candidates: created.update(
                relation=self.relate(extras, bead, candidates[0], "derived_from")),
        )
        before = self.row("SELECT count(*) FROM memoriesql.bead_versions")
        # The reverse assessed derived_from would close a cycle with the revision-6 row.
        self.activate_relations(ledger, (summary,))
        self.propose(lambda packet: [self.proposal(
            packet, "derived_from", self.endpoint(packet, ledger, 0), self.endpoint(packet, summary, 0),
            basis="agent_inferred")])
        self.run_relations(expect="failed_terminal")
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.bead_versions"), before)
        # Retiring the revision-6 row in the same write corrects the direction instead.
        self.activate_relations(ledger, (summary,), key="orchard.assess.corrected")
        self.propose(lambda packet: [self.proposal(
            packet, "derived_from", self.endpoint(packet, ledger, 0), self.endpoint(packet, summary, 0),
            basis="agent_inferred",
            retires={"relation_kind": "authored", "relation_id": created["relation"],
                     "reason": "The summary was mislabeled as the source."})])
        self.run_relations()
        authored = self.inspect(summary)
        (relation,) = authored.relations
        self.assertEqual((relation.state, relation.events[0].action), ("superseded", "retire"))

    def test_reconsideration_carries_the_recorded_disagreement_once(self) -> None:
        note = self.bead("Fictional frost F fell.", "Fictional buds B browned.", key="frost")
        first = self.activate_relations(note)
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, note, 1), self.endpoint(packet, note, 0),
            basis="agent_inferred")])
        self.specialist_plan = lambda packet: {"contract_version": 1, "judgments": [
            dict(j, consistent=False, rationale="The note never links them.")
            for j in self.agree(packet)["judgments"]]}
        self.run_relations()
        second = self.activate_relations(note, key="orchard.reconsider", reconsiders=first.task_id)
        self.assertEqual(second.reconsiders_task_id, first.task_id)
        self.author_plan = None
        self.specialist_plan = None
        self.run_relations()
        carried = self.frames[0]["reconsideration"]
        self.assertEqual(carried["reconsiders_task_id"], str(first.task_id))
        self.assertEqual(len(carried["proposals"]), 1)
        self.assertFalse(carried["judgments"][0]["consistent"])
        for key, target in (("orchard.again", first.task_id), ("orchard.chain", second.task_id)):
            with self.subTest(key), self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                self.activate_relations(note, key=key, reconsiders=target)

    def test_activation_pins_replays_and_refuses_conflicts(self) -> None:
        subject = self.bead("Fictional note N.", key="note")
        candidate = self.bead("Fictional note C.", key="candidate")
        receipt = self.activate_relations(subject, (candidate,))
        again = self.activate_relations(subject, (candidate,))
        self.assertTrue(again.replayed)
        self.assertEqual(again.task_id, receipt.task_id)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.activate_relations(subject)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.assessments.activate(ActivateRelationAssessment(
                idempotency_key="orchard.unknown.policy", subject_bead_id=subject,
                relation_vocabulary=VOCABULARY, dispatch_policy_id=uuid.uuid4()))
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.assessments.activate(ActivateRelationAssessment(
                idempotency_key="orchard.unknown.type", subject_bead_id=subject,
                relation_vocabulary=(RelationTypePin(key="unregistered", revision=1),),
                dispatch_policy_id=self.relation_policy))
        payload = self.row(
            "SELECT input_payload->'payload' FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (receipt.task_id,),
        )[0]
        self.assertEqual([b["role"] for b in payload["beads"]], ["subject", "candidate"])
        self.assertNotIn("claims", payload["beads"][0])
        self.assertEqual(len(payload["relation_vocabulary"]), len(VOCABULARY))

    def test_not_assessed_pairs_stay_distinct_from_not_related(self) -> None:
        # An unconsidered pair is never confused with one outside the scope.
        first = self.bead("Fictional well W ran dry.", key="well")
        second = self.bead("Fictional field F was sown.", key="field")
        self.activate_relations(first, (second,))

        def author(packet: Packet) -> dict[str, Any]:
            rows = self.dispositions(packet, [])
            for row in rows:
                if row["first_bead_id"] != row["second_bead_id"]:
                    row.update(disposition="not_assessed", reason="Out of time for this pair.")
            return {"proposals": [], "dispositions": rows}

        self.author_plan = author
        self.run_relations()
        dispositions = {(d.first_bead_id == d.second_bead_id, d.disposition, d.reason)
                        for d in self.inspect_v2(second).pair_dispositions}
        self.assertEqual(dispositions, {(True, "not_related", None),
                                        (False, "not_assessed", "Out of time for this pair.")})

    def test_another_tenant_cannot_activate_or_read_relation_tasks(self) -> None:
        subject = self.bead("Fictional private note.", key="private")
        self.activate_relations(subject)
        self.run_relations()
        workspace, _scope, secret, _lifecycle = self.second_tenant()
        other = PostgresRelationAssessments(self.db, credential_sha256=secret, workspace_id=workspace)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            other.activate(ActivateRelationAssessment(
                idempotency_key="orchard.foreign", subject_bead_id=subject,
                relation_vocabulary=VOCABULARY, dispatch_policy_id=self.relation_policy))
        self.assertEqual(other.inspect_relations(InspectBeadRelationsV2(bead_id=subject)).outcome,
                         "unavailable")

    def test_composition_without_an_attestor_dispatches_nothing(self) -> None:
        note = self.bead("Fictional cellar C flooded.", key="cellar")
        self.activate_relations(note)
        receipt = asyncio.run(self.relation_worker(recorder=None).run_once())
        self.assertNotEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents WHERE task_kind='memory.semantic.assess-relations'"), (0,))

    def test_the_claimant_cannot_attest_its_own_delivery(self) -> None:
        note = self.bead("Fictional attic A leaked.", key="attic")
        self.activate_relations(note)
        claimant = PostgresRelationDeliveryRecorder(
            connection_factory=self.connection, credential_sha256=self.worker_secret,
            workspace_id=self.workspace)
        receipt = asyncio.run(self.relation_worker(recorder=claimant).run_once())
        self.assertNotEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.relation_assessment_deliveries"), (0,))
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.assessed_relations"), (0,))

    def test_a_supervised_specialist_may_run_as_a_second_managed_turn(self) -> None:
        note = self.bead("Fictional sluice S opened.", "Fictional pond P rose.", key="sluice")
        receipt = self.activate_relations(note)
        self.propose(lambda packet: [self.proposal(
            packet, "caused_by", self.endpoint(packet, note, 1), self.endpoint(packet, note, 0),
            basis="agent_inferred")])
        target = CHARACTERIZED_TEST_REQUEST_TARGET.model_dump()
        for key in ("max_input_tokens_per_request", "max_output_tokens_per_request",
                    "transport_retries_disabled"):
            target.pop(key)
        managed = ManagedDispatchTarget(**target | {"usage_provenance": UsageProvenance.PROVIDER_REPORTED})
        approval = SupervisedQualification(
            qualification_id=uuid.uuid4(), semantic_task_id=receipt.task_id,
            routes=(
                SupervisedRoute(reference=RELATION_ASSESSMENT_TASK.model_profile, target=managed,
                                qualification_revision="fictional.author.v1"),
                SupervisedRoute(reference=SPECIALIST_PROFILE, target=managed,
                                qualification_revision="fictional.specialist.v1"),
            ),
            deadline=datetime.now(UTC) + timedelta(minutes=3),
            reported_input_token_stop=20000, reported_generated_token_stop=4000,
            reported_cash_stop_microunits=100000,
        )
        self.db.execute(
            "INSERT INTO memoriesql.model_supervised_qualifications(tenant_id,workspace_id,access_scope_id,qualification_id,task_id,origin_principal_id,configuration,approved_by_principal_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (self.tenant, self.workspace, self.scope, approval.qualification_id, receipt.task_id,
             self.principal, Jsonb(approval.model_dump(mode="json")), self.principal),
        )
        observation = ManagedUsageObservation(
            turn_completion="completed", reported_total=units(), underlying_inference_count=2,
            inference_count_basis="provider_reported")
        author = FictionalManagedModel(self.relation_response, observation)
        specialist = FictionalManagedModel(self.relation_response, observation)
        worker = self.relation_worker()
        cast(Any, worker._executor).model_profiles = PydanticAIModelProfileRegistry(tuple(
            ModelProfileBinding(
                reference=reference, model=model, request_target=managed, supervision=approval,
                real_model_admission=ManagedModelAdmission(model, reference, managed, revision))
            for reference, model, revision in (
                (RELATION_ASSESSMENT_TASK.model_profile, author, "fictional.author.v1"),
                (SPECIALIST_PROFILE, specialist, "fictional.specialist.v1"))))
        self.frames = []
        result = asyncio.run(worker.run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual((author.calls, specialist.calls), (1, 1))
        self.assertEqual(
            self.db.execute(
                "SELECT dispatch_boundary FROM memoriesql.model_dispatch_usage_fold_v2 WHERE supervised_qualification_id=%s",
                (approval.qualification_id,),
            ).fetchall(),
            [("managed_turn",), ("managed_turn",)],
        )
        (relation,) = self.inspect_v2(note).relations
        self.assertEqual(relation.state, "active")

    def test_a_later_type_revision_never_reinterprets_an_assertion(self) -> None:
        # Assertions keep their exact pinned revision and its definition.
        proposal = ProposeRelationType(
            idempotency_key="orchard.mitigates.1", access_scope_id=self.scope, key="mitigates",
            label="Mitigates", definition="The source's action reduces the target's fictional harm.",
            endpoint_rule="action -> harm", forward_reading="mitigates", inverse_reading="is mitigated by",
            symmetric=False, cycle_policy="permitted", reason="Fictional orchard vocabulary.",
        )

        def decide(revision: Any, key: str) -> None:
            self.lifecycle.decide_relation_type(DecideRelationType(
                idempotency_key=key, access_scope_id=self.scope, candidate_id=revision.candidate_id,
                decision="accepted", reason="The owner accepts the fictional type."))

        decide(self.lifecycle.propose_relation_type(proposal), "orchard.mitigates.1.decide")
        first = RelationTypePin(key="mitigates", revision=1)
        note = self.bead("Fictional shade cloth went up.", "Fictional heat stress eased.", key="shade")
        self.activate_relations(note, vocabulary=VOCABULARY + (first,))
        self.propose(lambda packet: [self.proposal(
            packet, "mitigates", self.endpoint(packet, note, 0), self.endpoint(packet, note, 1),
            basis="agent_inferred")])
        self.run_relations()
        decide(self.lifecycle.propose_relation_type(proposal.model_copy(update={
            "idempotency_key": "orchard.mitigates.2",
            "definition": "The source's action measurably reduces the target's fictional harm.",
            "reason": "Sharpen the fictional definition."})), "orchard.mitigates.2.decide")
        inspection = self.inspect_v2(note)
        (relation,) = inspection.relations
        self.assertEqual(relation.relation_type, first)
        (definition,) = [t for t in inspection.relation_types if t.key == "mitigates"]
        self.assertEqual((definition.revision, definition.definition),
                         (1, "The source's action reduces the target's fictional harm."))
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.activate_relations(note, key="orchard.assess.stale", vocabulary=VOCABULARY + (first,))
        self.activate_relations(note, key="orchard.assess.latest",
                                vocabulary=VOCABULARY + (RelationTypePin(key="mitigates", revision=2),))

    def test_accepted_assessed_derivation_feeds_bead_level_roots(self) -> None:
        quarry, mill = self.add_source("quarry"), self.add_source("mill")
        ledger = self.bead("Fictional quarry ledger: four crates.", key="ledger", source=quarry)
        copy_ = self.bead("Fictional mill copy: four crates.", key="copy", source=mill)

        def roots(bead: uuid.UUID) -> list[uuid.UUID]:
            return list(self.row(
                "SELECT memoriesql.bead_derivation_roots(%s,%s,clock_timestamp())", (self.tenant, bead))[0])

        self.assertEqual(roots(copy_), [mill])
        self.activate_relations(copy_, (ledger,))
        created: dict[str, str] = {}

        def derive(packet: Packet) -> list[dict[str, Any]]:
            proposal = self.proposal(packet, "derived_from", self.endpoint(packet, copy_, 0),
                                     self.endpoint(packet, ledger, 0), basis="agent_inferred")
            created["id"] = proposal["proposal_id"]
            return [proposal]

        self.propose(derive)
        self.run_relations()
        # The derivative inherits its source's root and adds none of its own.
        self.assertEqual(roots(copy_), [quarry])
        # A retired derivation stops counting at once.
        self.activate_relations(ledger, (copy_,), key="orchard.assess.unrelated")
        self.propose(lambda packet: [self.proposal(
            packet, "associated_with", self.endpoint(packet, ledger, 0), self.endpoint(packet, copy_, 0),
            basis="agent_inferred",
            retires={"relation_kind": "assessed", "relation_id": created["id"],
                     "reason": "The copy was transcribed independently."})])
        self.run_relations()
        self.assertEqual(roots(copy_), [mill])

    def test_a_null_time_never_passes_the_attempt_fence(self) -> None:
        # Probed on a live attempt: a real time passes, SQL NULL is refused.
        note = self.bead("Fictional kettle K boiled.", "Fictional window W fogged.", key="kettle")
        self.activate_relations(note)
        attestor = PostgresRelationDeliveryRecorder(
            connection_factory=self.connection, credential_sha256=self.attestor_secret,
            workspace_id=self.workspace)
        outcomes: dict[str, tuple[Any, Any]] = {}
        test = self

        class Probe:
            async def record_relation_delivery(self, **kwargs: Any) -> None:
                await attestor.record_relation_delivery(**kwargs)
                if kwargs["decision"] is None:
                    outcomes.update(test.probe_null_times())

        receipt = asyncio.run(self.relation_worker(recorder=Probe()).run_once())
        self.assertEqual(receipt.task_status, "succeeded", receipt)
        self.assertEqual(outcomes, {"reauthorize": ("authorized", "invalid_phase"),
                                    "run_event": (True, "refused")})

    def probe_null_times(self) -> dict[str, tuple[Any, Any]]:
        task, attempt, generation = self.row(
            "SELECT a.task_id, a.attempt_id, a.lease_generation FROM memoriesql.semantic_task_attempts AS a "
            "JOIN memoriesql.semantic_tasks AS q USING (tenant_id, task_id) "
            "WHERE q.task_kind = 'memory.semantic.assess-relations' AND a.status = 'running'")
        (run,) = self.row(
            "SELECT run_id FROM memoriesql.semantic_task_runs WHERE attempt_id=%s AND parent_run_id IS NULL",
            (attempt,))
        task_input = RELATION_ASSESSMENT_TASK.input_contract.reference
        output = RELATION_ASSESSMENT_TASK.output_contract.reference
        profile = RELATION_ASSESSMENT_TASK.model_profile
        fence = (self.tenant, task, attempt, generation, "orchard.worker", "orchard.instance")
        with self.connection() as connection, connection.transaction():
            connection.execute("SET LOCAL ROLE memoriesql_worker")
            PostgresAuthorizationPort(connection).begin_context(
                credential_sha256=self.worker_secret, requested_workspace_id=self.workspace)

            def reauthorize(at: Any) -> Any:
                return connection.execute(
                    "SELECT memoriesql.reauthorize_semantic_task(%s,%s,%s,%s,%s,%s,'hydrate',%s)",
                    fence + (at,)).fetchone()[0]

            def replay(at: Any) -> Any:
                # An exact replay of the live root run's start event.
                return connection.execute(
                    "SELECT memoriesql.record_semantic_run_event(%s,%s,%s,%s,%s,%s,'run.started',%s,NULL,"
                    "'direct_leaf',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s)",
                    fence + (run, RELATION_ASSESSMENT_TASK.leaf_agent_key, task_input.contract_id,
                             task_input.revision, task_input.schema_hash, output.contract_id,
                             output.revision, output.schema_hash, profile.profile_key, profile.revision,
                             profile.effort_key, at)).fetchone()[0]

            def refused(at: Any) -> Any:
                try:
                    with connection.transaction():
                        return replay(at)
                except psycopg.errors.InvalidParameterValue:
                    return "refused"

            now = datetime.now(UTC)
            return {"reauthorize": (reauthorize(now), reauthorize(None)),
                    "run_event": (replay(now), refused(None))}

    def test_vocabulary_read_lists_active_definitions(self) -> None:
        inspection = self.assessments.inspect_vocabulary(InspectRelationVocabulary())
        self.assertEqual(inspection.outcome, "available")
        self.assertEqual({t.key for t in inspection.relation_types}, set(relation_fixtures.BUILT_INS))
        for definition in inspection.relation_types:
            self.assertTrue(definition.definition and definition.endpoint_rule)

    def test_new_tables_force_row_security_and_grant_nothing(self) -> None:
        for table in NEW_TABLES:
            row = self.row(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (f"memoriesql.{table}",),
            )
            self.assertEqual(row, (True, True), table)
            for role in ("memoriesql_application", "memoriesql_worker"):
                self.assertFalse(
                    self.row(
                        "SELECT has_table_privilege(%s, %s, 'SELECT,INSERT,UPDATE,DELETE')",
                        (role, f"memoriesql.{table}"),
                    )[0],
                    (table, role),
                )


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        RelationAssessment(name)
        for name in RelationAssessment.__dict__
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
