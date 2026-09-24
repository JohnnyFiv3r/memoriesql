"""Relation assessment after acceptance: a separate follow-up task (schema 29).

A relation task runs only after its subject bead version is accepted and only
against immutable accepted versions. It never adds statements, never changes a
bead and never vetoes acceptance: a refused, failed or paused task leaves every
bead and receipt untouched and keeps its own visible status. The first revision
is limited to relations; tracked claims and claim updates wait for a later
revision, while beads keep recording them as statements.

The author proposes exact assertions and a disposition for every unordered pair
of pinned beads, each bead with itself included. A provider-neutral specialist
then assesses each exact proposed assertion against every pinned definition and
the same authorized evidence. Code accepts a proposal only when the specialist
finds that exact assertion consistent and its warranted set contains the
author's predicate, revision and direction. Disagreement and abstention leave
the proposal unaccepted with both contributions recorded; there is no retry
loop, relabeling or code voting. Author confidence is a diagnostic, never
authority.
"""

from __future__ import annotations

import hashlib
from typing import Final, Literal, Protocol, cast, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from memoriesql.application.authored_relations import (
    BeadTypeReference,
    CandidateMention,
    RelationTypeDefinition,
    RelationTypePin,
    SourceClock,
)
from memoriesql.application.builtin_semantic_tasks import (
    BUILTIN_EFFORT_PROFILES,
    BUILTIN_MODEL_PROFILE_REFERENCES,
)
from memoriesql.application.canonical_transactions import (
    StatementEvidence,
    StatementKind,
)
from memoriesql.application.module_registry import BuiltInModuleRegistry
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    DispatchMode,
    EvidencePolicyContract,
    FrozenContractModel,
    ModelProfileReference,
    RunBudget,
    SemanticTaskDefinition,
    SemanticTaskInput,
    TypedModelContract,
    canonical_json_bytes,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry

ASSESSMENT_KIND: Final = "memory.semantic.assess-relations"
MAX_CANDIDATES = 8
MAX_BEADS = MAX_CANDIDATES + 1
# Every unordered pair of pinned beads, each bead with itself included.
MAX_PAIRS = MAX_BEADS * (MAX_BEADS + 1) // 2
MAX_PROPOSALS = 16
MAX_EVIDENCE_UNITS = 72
EVIDENCE_CHARACTERS = 131072
PACKET_BYTES = 1048576
OUTPUT_BYTES = 131072
SPECIALIST_DECISION_BYTES = 65536

Abstention = Literal["no_fit", "insufficient_evidence", "ambiguous"]


def _nonblank(value: str | None, field: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field} must not be blank")


def digest(value: object) -> str:
    """SHA-256 of canonical JSON, the hash every packet and contribution uses."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class PinnedStatement(FrozenContractModel):
    statement_id: UUID
    statement_kind: StatementKind
    statement_text: str = Field(min_length=1, max_length=8192)
    evidence: tuple[StatementEvidence, ...] = Field(min_length=1, max_length=64)


class PinnedBead(FrozenContractModel):
    """One accepted bead version exactly as pinned at activation.

    The subject is always first. Supplied candidates are bounded coverage, never
    a relevance ranking, and no other bead is searched.
    """

    role: Literal["subject", "candidate"]
    bead_id: UUID
    bead_version_id: UUID
    origin_kind: Literal["initial", "correction"]
    bead_type: BeadTypeReference
    title: str | None = Field(default=None, max_length=2048)
    summary: tuple[str, ...] = Field(default=(), max_length=3)
    source: SourceClock
    statements: tuple[PinnedStatement, ...] = Field(min_length=1, max_length=32)
    mentions: tuple[CandidateMention, ...] = Field(default=(), max_length=32)
    superseded_by: tuple[UUID, ...] = Field(default=(), max_length=8)


class StatementPin(FrozenContractModel):
    bead_id: UUID
    bead_version_id: UUID
    statement_id: UUID


class RelationEndpoint(FrozenContractModel):
    """An accepted pinned bead version and one to eight of its statements."""

    bead_id: UUID
    bead_version_id: UUID
    statement_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique(self) -> RelationEndpoint:
        if len(self.statement_ids) != len(set(self.statement_ids)):
            raise ValueError("endpoint statement IDs must be unique")
        return self


class RelationEvidencePin(FrozenContractModel):
    """One exact statement-evidence pair of an endpoint or basis statement."""

    statement_id: UUID
    source_unit_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RetiredAssertion(FrozenContractModel):
    """An earlier active assertion this proposal replaces in the same apply.

    It takes effect only when the replacing proposal is accepted.
    """

    relation_kind: Literal["authored", "assessed"]
    relation_id: UUID
    reason: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def shape(self) -> RetiredAssertion:
        _nonblank(self.reason, "retirement reason")
        return self


class RelationProposal(FrozenContractModel):
    """One exact proposed assertion: "source <forward reading> target".

    Neither endpoint has to be the subject bead, and both may sit in one bead
    when their statements differ. Basis statements, separate from both
    endpoints, state or support the relationship and carry its attribution; a
    source-stated assertion names at least one.
    """

    proposal_id: UUID
    relation_type: RelationTypePin
    source: RelationEndpoint
    target: RelationEndpoint
    basis_statements: tuple[StatementPin, ...] = Field(default=(), max_length=8)
    evidence: tuple[RelationEvidencePin, ...] = Field(min_length=1, max_length=8)
    basis: Literal["source_stated", "agent_inferred"]
    # Material conditions, scope and hedging of the assertion.
    qualification: str | None = Field(default=None, min_length=1, max_length=1024)
    rationale: str = Field(min_length=1, max_length=1024)
    # Diagnostic 0-1 author confidence with at most two decimals; never authority.
    author_confidence: float = Field(ge=0, le=1, multiple_of=0.01, allow_inf_nan=False)
    retires: RetiredAssertion | None = None

    @model_validator(mode="after")
    def shape(self) -> RelationProposal:
        _nonblank(self.rationale, "relation rationale")
        _nonblank(self.qualification, "relation qualification")
        # Two decimals keep Python and PostgreSQL canonical JSON byte-identical.
        if round(self.author_confidence, 2) != self.author_confidence:
            raise ValueError("author confidence uses at most two decimal places")
        source = {(self.source.bead_id, s) for s in self.source.statement_ids}
        target = {(self.target.bead_id, s) for s in self.target.statement_ids}
        source_ids = set(self.source.statement_ids)
        target_ids = set(self.target.statement_ids)
        if source_ids & target_ids or source & target:
            raise ValueError("endpoint statements must differ")
        basis_ids = [b.statement_id for b in self.basis_statements]
        if len(basis_ids) != len(set(basis_ids)):
            raise ValueError("basis statement IDs must be unique")
        if set(basis_ids) & (source_ids | target_ids):
            raise ValueError("basis statements are separate from both endpoints")
        if self.basis == "source_stated" and not self.basis_statements:
            raise ValueError("a source-stated assertion names the statement stating it")
        named = source_ids | target_ids | set(basis_ids)
        if any(e.statement_id not in named for e in self.evidence):
            raise ValueError("evidence belongs to an endpoint or basis statement")
        pins = [(e.statement_id, e.source_unit_id) for e in self.evidence]
        if len(pins) != len(set(pins)):
            raise ValueError("relation evidence pins must be unique")
        return self


class PairDisposition(FrozenContractModel):
    """The author's disposition of one unordered pair of pinned beads.

    `not_assessed` stays valid and is never unrelated. A pair's disposition does
    not show that every statement pair in it was considered.
    """

    first_bead_id: UUID
    second_bead_id: UUID
    disposition: Literal["related", "not_related", "abstained", "not_assessed"]
    abstention: Abstention | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def shape(self) -> PairDisposition:
        _nonblank(self.reason, "disposition reason")
        if str(self.first_bead_id) > str(self.second_bead_id):
            raise ValueError("a pair names its beads in canonical order")
        if (self.disposition == "abstained") != (self.abstention is not None):
            raise ValueError("only an abstention names no fit, insufficient evidence or ambiguity")
        if self.disposition == "not_assessed" and self.reason is None:
            raise ValueError("a pair that was not assessed requires its reason")
        return self


class RelationAuthorOutput(FrozenContractModel):
    """The author's proposals and a disposition for every pinned pair."""

    proposals: tuple[RelationProposal, ...] = Field(max_length=MAX_PROPOSALS)
    dispositions: tuple[PairDisposition, ...] = Field(min_length=1, max_length=MAX_PAIRS)

    @model_validator(mode="after")
    def coherent(self) -> RelationAuthorOutput:
        ids = [p.proposal_id for p in self.proposals]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal IDs must be unique")
        labels = [
            (
                p.relation_type.key,
                p.source.bead_id,
                tuple(sorted(map(str, p.source.statement_ids))),
                p.target.bead_id,
                tuple(sorted(map(str, p.target.statement_ids))),
            )
            for p in self.proposals
        ]
        if len(labels) != len(set(labels)):
            raise ValueError("one proposal per predicate and exact endpoints")
        retired = [
            (p.retires.relation_kind, p.retires.relation_id)
            for p in self.proposals
            if p.retires is not None
        ]
        if len(retired) != len(set(retired)):
            raise ValueError("an assertion is retired at most once")
        pairs = [(d.first_bead_id, d.second_bead_id) for d in self.dispositions]
        if len(pairs) != len(set(pairs)):
            raise ValueError("one disposition per pinned pair")
        related = {
            tuple(sorted((p.source.bead_id, p.target.bead_id), key=str))
            for p in self.proposals
        }
        for d in self.dispositions:
            if (d.disposition == "related") != ((d.first_bead_id, d.second_bead_id) in related):
                raise ValueError("a related disposition requires, and only it permits, a proposal")
        if not related <= set(pairs):
            raise ValueError("every related pair requires its disposition")
        return self


class WarrantedRelation(FrozenContractModel):
    relation_type: RelationTypePin
    direction: Literal["as_proposed", "reversed"]


class RelationJudgment(FrozenContractModel):
    """The specialist's assessment of one exact proposed assertion.

    `consistent` covers the whole assertion: endpoints, basis statements,
    attribution, qualification, rationale, predicate, revision and direction.
    `warranted` lists every predicate, revision and direction the specialist
    finds warranted over the same endpoints, possibly none.
    """

    proposal_id: UUID
    outcome: Literal["assessed", "abstained"]
    consistent: bool | None = None
    warranted: tuple[WarrantedRelation, ...] = Field(default=(), max_length=32)
    abstention: Abstention | None = None
    rationale: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def shape(self) -> RelationJudgment:
        _nonblank(self.rationale, "judgment rationale")
        if self.outcome == "assessed":
            if self.consistent is None or self.abstention is not None:
                raise ValueError("an assessment states consistency and abstains from nothing")
        elif self.consistent is not None or self.warranted or self.abstention is None:
            raise ValueError("an abstention names its reason and warrants nothing")
        keys = [(w.relation_type.key, w.direction) for w in self.warranted]
        if len(keys) != len(set(keys)):
            raise ValueError("warranted predicates must be unique")
        return self

    def agrees_with(self, proposal: RelationProposal) -> bool:
        return (
            self.proposal_id == proposal.proposal_id
            and self.outcome == "assessed"
            and self.consistent is True
            and WarrantedRelation(relation_type=proposal.relation_type, direction="as_proposed")
            in self.warranted
        )


class RelationSpecialistDecision(FrozenContractModel):
    """One specialist batch: a judgment for every proposal it was given."""

    contract_version: Literal[1] = 1
    judgments: tuple[RelationJudgment, ...] = Field(min_length=1, max_length=MAX_PROPOSALS)

    @model_validator(mode="after")
    def bounded(self) -> RelationSpecialistDecision:
        ids = [j.proposal_id for j in self.judgments]
        if len(ids) != len(set(ids)):
            raise ValueError("one judgment per proposal")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > SPECIALIST_DECISION_BYTES:
            raise ValueError(
                f"specialist decision exceeds {SPECIALIST_DECISION_BYTES} bytes; never truncated"
            )
        return self


class RelationSpecialistContribution(FrozenContractModel):
    """One attributable specialist contribution; request_id is its stable identity."""

    contract_version: Literal[1] = 1
    request_id: UUID
    model_run_ref: str = Field(min_length=1, max_length=512)
    author_run_ref: str = Field(min_length=1, max_length=512)
    packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposals_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    vocabulary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: RelationSpecialistDecision

    @model_validator(mode="after")
    def distinct_runs(self) -> RelationSpecialistContribution:
        if self.model_run_ref == self.author_run_ref:
            raise ValueError("specialist and author must have distinct attributable runs")
        return self


class RelationAssessmentOutput(RelationAuthorOutput):
    """The applied result: the author's output and every specialist contribution.

    Batching is a profile choice: every proposal is judged exactly once across
    the contributions, and there is no contribution without a proposal.
    """

    specialist_contributions: tuple[RelationSpecialistContribution, ...] = Field(
        max_length=MAX_PROPOSALS
    )

    @model_validator(mode="after")
    def every_proposal_judged(self) -> RelationAssessmentOutput:
        judged = [
            j.proposal_id
            for c in self.specialist_contributions
            for j in c.decision.judgments
        ]
        if sorted(map(str, judged)) != sorted(str(p.proposal_id) for p in self.proposals):
            raise ValueError("every proposal is judged exactly once, and only proposals are")
        requests = [c.request_id for c in self.specialist_contributions]
        if len(requests) != len(set(requests)):
            raise ValueError("specialist contributions must be distinct")
        if len({c.author_run_ref for c in self.specialist_contributions}) > 1:
            raise ValueError("every contribution assesses one author run")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > OUTPUT_BYTES:
            raise ValueError(f"relation assessment output exceeds {OUTPUT_BYTES} bytes")
        return self

    def accepted(self) -> frozenset[UUID]:
        judgments = {
            j.proposal_id: j
            for c in self.specialist_contributions
            for j in c.decision.judgments
        }
        return frozenset(
            p.proposal_id
            for p in self.proposals
            if judgments[p.proposal_id].agrees_with(p)
        )


class Reconsideration(FrozenContractModel):
    """The recorded disagreement an explicitly linked reconsideration carries."""

    reconsiders_task_id: UUID
    proposals: tuple[RelationProposal, ...] = Field(min_length=1, max_length=MAX_PROPOSALS)
    judgments: tuple[RelationJudgment, ...] = Field(min_length=1, max_length=MAX_PROPOSALS)

    @model_validator(mode="after")
    def unaccepted_only(self) -> Reconsideration:
        judgments = {j.proposal_id: j for j in self.judgments}
        if set(judgments) != {p.proposal_id for p in self.proposals}:
            raise ValueError("a reconsideration carries each prior judgment")
        if any(judgments[p.proposal_id].agrees_with(p) for p in self.proposals):
            raise ValueError("a reconsideration carries only unaccepted proposals")
        return self


class RelationAssessmentPayload(FrozenContractModel):
    subject_bead_id: UUID
    beads: tuple[PinnedBead, ...] = Field(min_length=1, max_length=MAX_BEADS)
    relation_vocabulary: tuple[RelationTypeDefinition, ...] = Field(min_length=1, max_length=32)
    dispatch_policy_id: UUID
    reconsideration: Reconsideration | None = None
    required_execution: Literal["trusted_relation_assessment_v1"] = (
        "trusted_relation_assessment_v1"
    )

    @model_validator(mode="after")
    def pins(self) -> RelationAssessmentPayload:
        roles = [b.role for b in self.beads]
        if roles[0] != "subject" or "subject" in roles[1:]:
            raise ValueError("the subject is the first and only subject bead")
        if self.beads[0].bead_id != self.subject_bead_id:
            raise ValueError("the first pinned bead is the subject")
        beads = [b.bead_id for b in self.beads]
        if len(beads) != len(set(beads)):
            raise ValueError("pinned beads must be distinct")
        statements = [s.statement_id for b in self.beads for s in b.statements]
        if len(statements) != len(set(statements)):
            raise ValueError("pinned statements must be distinct")
        keys = [d.key for d in self.relation_vocabulary]
        if len(keys) != len(set(keys)):
            raise ValueError("one explicit revision per relation vocabulary key")
        return self

    def evidence_units(self) -> dict[UUID, str]:
        units: dict[UUID, str] = {}
        for bead in self.beads:
            for statement in bead.statements:
                for pin in statement.evidence:
                    if units.setdefault(pin.source_unit_id, pin.content_hash) != pin.content_hash:
                        raise ValueError("one evidence unit has one content hash")
        return units


def required_pairs(beads: tuple[PinnedBead, ...]) -> frozenset[tuple[UUID, UUID]]:
    """Every unordered pair of pinned beads, each bead with itself, in canonical order."""

    ids = sorted((b.bead_id for b in beads), key=str)
    return frozenset(
        (first, second) for i, first in enumerate(ids) for second in ids[i:]
    )


class RelationAssessmentInput(SemanticTaskInput[RelationAssessmentPayload]):
    task_kind: Literal["memory.semantic.assess-relations"] = ASSESSMENT_KIND
    contract_revision: Literal[1] = 1
    requested_effort_key: None = None
    requested_budget: None = None
    expected_target_revision: Literal[0] = 0

    @model_validator(mode="after")
    def exact_pins(self) -> RelationAssessmentInput:
        if self.target_reference != str(self.payload.subject_bead_id):
            raise ValueError("a relation assessment targets its subject bead")
        units = self.payload.evidence_units()
        references = {
            r.reference_id: r.content_hash for r in self.evidence_manifest.references
        }
        if references != {str(unit): content for unit, content in units.items()}:
            raise ValueError("the manifest names exactly the pinned statements' evidence units")
        if (
            len(references) > MAX_EVIDENCE_UNITS
            or sum(r.declared_characters for r in self.evidence_manifest.references)
            > EVIDENCE_CHARACTERS
        ):
            raise ValueError("relation_assessment_evidence_budget")
        return self


class EvidencePart(FrozenContractModel):
    ordinal: int = Field(ge=0, lt=256)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(min_length=1)


class EvidenceExcerpt(FrozenContractModel):
    """The exact authorized content behind one evidence pin.

    Pins name whole units: an observation unit's text, or every normalized part
    of a complete unit's sealed package. There is no narrower excerpt.
    """

    source_unit_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    representation: Literal["observation_text", "sealed_package"]
    content: str | None = None
    parts: tuple[EvidencePart, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def shape(self) -> EvidenceExcerpt:
        if self.representation == "observation_text":
            if self.content is None or self.parts:
                raise ValueError("an observation unit carries its text")
            if hashlib.sha256(self.content.encode()).hexdigest() != self.content_hash:
                raise ValueError("observation text must match its pinned hash")
        elif self.content is not None or not self.parts:
            raise ValueError("a sealed package carries its parts")
        for part in self.parts:
            if hashlib.sha256(part.content.encode()).hexdigest() != part.content_sha256:
                raise ValueError("package part must match its hash")
        if [p.ordinal for p in self.parts] != list(range(len(self.parts))):
            raise ValueError("package parts are complete and in order")
        return self

    @property
    def characters(self) -> int:
        if self.content is not None:
            return len(self.content)
        return sum(len(p.content) for p in self.parts)


class RelationAuthorPacket(FrozenContractModel):
    """What the author receives: the pinned beads, definitions and evidence."""

    contract_version: Literal[1] = 1
    role: Literal["author"] = "author"
    task_id: UUID
    attempt_id: UUID
    subject_bead_id: UUID
    beads: tuple[PinnedBead, ...] = Field(min_length=1, max_length=MAX_BEADS)
    relation_vocabulary: tuple[RelationTypeDefinition, ...] = Field(min_length=1, max_length=32)
    evidence: tuple[EvidenceExcerpt, ...] = Field(min_length=1, max_length=MAX_EVIDENCE_UNITS)
    reconsideration: Reconsideration | None = None

    @model_validator(mode="after")
    def bounded(self) -> RelationAuthorPacket:
        _packet_bounds(self, self.evidence)
        return self


class RelationSpecialistPacket(FrozenContractModel):
    """What the specialist receives: the author's packet plus its proposals."""

    contract_version: Literal[1] = 1
    role: Literal["specialist"] = "specialist"
    task_id: UUID
    attempt_id: UUID
    author_run_id: str = Field(min_length=1, max_length=512)
    subject_bead_id: UUID
    beads: tuple[PinnedBead, ...] = Field(min_length=1, max_length=MAX_BEADS)
    relation_vocabulary: tuple[RelationTypeDefinition, ...] = Field(min_length=1, max_length=32)
    evidence: tuple[EvidenceExcerpt, ...] = Field(min_length=1, max_length=MAX_EVIDENCE_UNITS)
    proposals: tuple[RelationProposal, ...] = Field(min_length=1, max_length=MAX_PROPOSALS)

    @model_validator(mode="after")
    def bounded(self) -> RelationSpecialistPacket:
        _packet_bounds(self, self.evidence)
        return self


def _packet_bounds(packet: BaseModel, evidence: tuple[EvidenceExcerpt, ...]) -> None:
    units = [e.source_unit_id for e in evidence]
    if len(units) != len(set(units)):
        raise ValueError("each evidence unit is delivered once")
    if sum(e.characters for e in evidence) > EVIDENCE_CHARACTERS:
        raise ValueError(
            f"relation evidence exceeds {EVIDENCE_CHARACTERS} characters; never truncated"
        )
    if len(canonical_json_bytes(packet.model_dump(mode="json"))) > PACKET_BYTES:
        raise ValueError(f"relation packet exceeds {PACKET_BYTES} bytes; never truncated")


class RelationAuthorStep(FrozenContractModel):
    """The author's typed control output; notes have no canonical authority."""

    action: Literal["finish", "incomplete"]
    notes: str = Field(default="", max_length=4096)
    typed_output: RelationAuthorOutput | None = None

    @model_validator(mode="after")
    def selected_action(self) -> RelationAuthorStep:
        if (self.action == "finish") != (self.typed_output is not None):
            raise ValueError("only finish carries semantic output")
        return self


SPECIALIST_KEY: Final = "memory.semantic.relation-specialist"
SPECIALIST_PROFILE = ModelProfileReference(
    profile_key="relation-specialist.standard", revision=1, effort_key="standard"
)
SPECIALIST_INPUT = TypedModelContract(
    contract_id="memory.semantic.relation-assessment.packet",
    revision=1,
    model_type=RelationSpecialistPacket,
)
SPECIALIST_OUTPUT = TypedModelContract(
    contract_id="memory.semantic.relation-assessment.decision",
    revision=1,
    model_type=RelationSpecialistDecision,
)
SPECIALIST_AGENT = AgentContract(
    agent_key=SPECIALIST_KEY,
    input_contract=SPECIALIST_INPUT.reference,
    output_contract=SPECIALIST_OUTPUT.reference,
    maximum_effort_key="standard",
)

RELATION_ASSESSMENT_TASK = SemanticTaskDefinition(
    task_kind=ASSESSMENT_KIND,
    contract_revision=1,
    owning_module="memoriesql.kernel",
    input_contract=TypedModelContract(
        contract_id="memory.semantic.relation-assessment.input",
        revision=1,
        model_type=RelationAssessmentInput,
    ),
    output_contract=TypedModelContract(
        contract_id="memory.semantic.relation-assessment.output",
        revision=1,
        model_type=RelationAssessmentOutput,
    ),
    dispatch_mode=DispatchMode.DIRECT_LEAF,
    default_effort_key="standard",
    maximum_effort_key="standard",
    model_profile=ModelProfileReference(
        profile_key="quality-first.standard", revision=1, effort_key="standard"
    ),
    evidence_policy_key="relation-assessment-v1",
    # One fixed-packet author request and one batched specialist request. This
    # ceiling is not disclosure approval or a model choice; explicit activation,
    # an attestor policy and composition are required.
    run_budget=RunBudget(
        wall_clock_seconds=900,
        request_limit=2,
        input_token_limit=1048576,
        output_token_limit=65536,
        total_token_limit=1114112,
        tool_call_limit=0,
        max_delegate_calls=1,
        max_parallel_delegates=1,
        evidence_item_limit=MAX_EVIDENCE_UNITS,
        hydrated_character_limit=EVIDENCE_CHARACTERS,
        output_retries=0,
        tool_retries=0,
    ),
    leaf_agent_key="memory.semantic.relation-author",
    allowed_delegate_keys=(SPECIALIST_KEY,),
)


def load_relation_assessment_task_registry(
    module_registry: BuiltInModuleRegistry,
) -> SemanticTaskRegistry:
    task = RELATION_ASSESSMENT_TASK
    return SemanticTaskRegistry._from_source_controlled(
        module_registry,
        (cast(SemanticTaskDefinition[BaseModel, BaseModel], task),),
        (
            AgentContract(
                agent_key=cast(str, task.leaf_agent_key),
                input_contract=task.input_contract.reference,
                output_contract=task.output_contract.reference,
                maximum_effort_key="standard",
            ),
            SPECIALIST_AGENT,
        ),
        (),
        BUILTIN_EFFORT_PROFILES,
        (*BUILTIN_MODEL_PROFILE_REFERENCES, SPECIALIST_PROFILE),
        (EvidencePolicyContract(policy_key="relation-assessment-v1"),),
        leaf_contracts=(
            cast(TypedModelContract[BaseModel], SPECIALIST_INPUT),
            cast(TypedModelContract[BaseModel], SPECIALIST_OUTPUT),
        ),
    )


class ActivateRelationAssessment(FrozenContractModel):
    """Pin one accepted subject, explicit candidates and exact vocabulary revisions.

    An empty candidate set is valid: the subject's own statements can relate.
    A reconsideration names the earlier assessment whose unaccepted proposals it
    carries and pins the same beads and vocabulary.
    """

    contract_version: Literal[1] = 1
    expected_schema_version: Literal[29] = 29
    idempotency_key: str = Field(min_length=1, max_length=512)
    subject_bead_id: UUID
    candidate_bead_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_CANDIDATES)
    relation_vocabulary: tuple[RelationTypePin, ...] = Field(min_length=1, max_length=32)
    dispatch_policy_id: UUID
    reconsiders_task_id: UUID | None = None

    @model_validator(mode="after")
    def distinct(self) -> ActivateRelationAssessment:
        if len(set(self.candidate_bead_ids)) != len(self.candidate_bead_ids):
            raise ValueError("candidate beads must be distinct")
        if self.subject_bead_id in self.candidate_bead_ids:
            raise ValueError("the subject is not its own candidate")
        keys = [p.key for p in self.relation_vocabulary]
        if len(keys) != len(set(keys)):
            raise ValueError("one explicit revision per relation vocabulary key")
        return self


class RelationAssessmentActivationReceipt(FrozenContractModel):
    contract_version: Literal[1] = 1
    task_id: UUID
    subject_bead_id: UUID
    subject_bead_version_id: UUID
    candidate_bead_ids: tuple[UUID, ...]
    reconsiders_task_id: UUID | None
    idempotency_receipt_id: UUID
    enqueue_receipt_id: UUID
    replayed: bool


class ApplyRelationAssessment(FrozenContractModel):
    contract_version: Literal[1] = 1
    expected_schema_version: Literal[29] = 29
    idempotency_key: str = Field(min_length=1, max_length=512)
    tenant_id: UUID
    workspace_id: UUID
    access_scope_id: UUID
    task_id: UUID
    attempt_id: UUID
    lease_generation: int = Field(ge=1)
    task_kind: Literal["memory.semantic.assess-relations"] = ASSESSMENT_KIND
    contract_revision: Literal[1] = 1
    output_contract_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_result_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_payload_canonical_json: str = Field(min_length=2, max_length=OUTPUT_BYTES)
    used_evidence_refs: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_UNITS)
    model_run_refs: tuple[str, ...] = Field(min_length=1, max_length=1 + MAX_PROPOSALS)
    payload: RelationAssessmentOutput

    @model_validator(mode="after")
    def exact_result(self) -> ApplyRelationAssessment:
        data = canonical_json_bytes(self.payload.model_dump(mode="json"))
        if (
            self.semantic_payload_canonical_json.encode() != data
            or hashlib.sha256(data).hexdigest() != self.semantic_result_hash
        ):
            raise ValueError("semantic result hash mismatch")
        if self.output_contract_hash != RELATION_ASSESSMENT_TASK.output_contract.schema_hash:
            raise ValueError("relation assessment output contract mismatch")
        runs = {c.author_run_ref for c in self.payload.specialist_contributions} | {
            c.model_run_ref for c in self.payload.specialist_contributions
        }
        if runs - set(self.model_run_refs):
            raise ValueError("contributions must bind this exact run tree")
        return self


@runtime_checkable
class RelationAssessmentAccess(Protocol):
    """Trusted composition only; never exposed as a model tool or input field."""

    async def evidence(self) -> tuple[EvidenceExcerpt, ...]: ...
    async def authorize_delivery(
        self, packet: RelationAuthorPacket | RelationSpecialistPacket
    ) -> None: ...


@runtime_checkable
class RelationDeliveryRecorder(Protocol):
    """The attestor records what one provider request actually received."""

    async def record_relation_delivery(
        self,
        *,
        request_id: UUID,
        request_payload_hash: str,
        packet: RelationAuthorPacket | RelationSpecialistPacket,
        decision: RelationSpecialistDecision | None,
    ) -> None: ...


class RelationAssessments(Protocol):
    def activate(
        self, command: ActivateRelationAssessment
    ) -> RelationAssessmentActivationReceipt: ...
