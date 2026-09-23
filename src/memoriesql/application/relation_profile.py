"""Relation semantic profile, revision 1: the family projection over built-in predicates.

A relationship has three layers. The predicate is the registered relation type with
its exact, versioned meaning (built-in revision 1 of the relation vocabulary). The
family organizes navigation and presentation only: it never proves causality,
identity or support, never gates authoring or acceptance, and never makes a
filtered result look complete. The sign is a reading (+ forward, - inverse), not a
confidence or a traversal cost. The basis belongs to each assertion.

The family codes, readings and arrow aliases follow Mark Burgess's Semantic
Spacetime. This profile is separately versioned, so a mapping change never becomes
a meaning revision and never reinterprets a stored assertion.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from memoriesql.application.semantic_task_contracts import FrozenContractModel

Family = Literal["NEAR", "LEADSTO", "CONTAINS", "EXPRESS"]
MappingStatus = Literal[
    "established",
    "provisional",
    "provisional_local_extension",
    "unresolved",
    "native_unresolved",
    "intentionally_native",
]
SSTORYTIME_COMMIT = "039f61003c9b4a93fff88e5ef3be4071ac27db72"
BUILT_IN_KEYS = (
    "supports",
    "contradicts",
    "caused_by",
    "led_to",
    "enables",
    "part_of",
    "depends_on",
    "blocks",
    "derived_from",
    "supersedes",
    "associated_with",
)


class FamilyCode(FrozenContractModel):
    family: Family
    magnitude: int = Field(ge=0, le=3)


class RelationFamilyMapping(FrozenContractModel):
    """How one built-in predicate maps to a family; the predicate's meaning is unchanged."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    family: Family | None
    orientation: Literal[-3, -2, -1, 0, 1, 2, 3] | None
    mapping_status: MappingStatus
    # SSTconfig/arrows-<family>.sst file:line at the pinned upstream commit.
    upstream_anchor: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def shape(self) -> RelationFamilyMapping:
        if (self.family is None) != (self.orientation is None):
            raise ValueError("a family and its orientation are given together")
        return self


class RelationSemanticProfile(FrozenContractModel):
    profile_revision: Literal[1] = 1
    # The built-in relation type revision whose predicates this profile maps.
    definitions_revision: Literal[1] = 1
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    families: tuple[FamilyCode, ...]
    rules: tuple[str, ...]
    mappings: tuple[RelationFamilyMapping, ...]
    attribution: str

    @model_validator(mode="after")
    def complete(self) -> RelationSemanticProfile:
        if tuple(m.key for m in self.mappings) != BUILT_IN_KEYS:
            raise ValueError("the profile maps exactly the eleven built-in keys, in order")
        return self


RELATION_SEMANTIC_PROFILE = RelationSemanticProfile(
    upstream_commit=SSTORYTIME_COMMIT,
    families=(
        FamilyCode(family="NEAR", magnitude=0),
        FamilyCode(family="LEADSTO", magnitude=1),
        FamilyCode(family="CONTAINS", magnitude=2),
        FamilyCode(family="EXPRESS", magnitude=3),
    ),
    rules=(
        "An inverse reading is the same assertion, never a second assertion or "
        "independent corroboration. Separately authored assertions are never merged "
        "because their readings look inverse.",
        "associated_with and contradicts are symmetric: one assertion covers both "
        "directions, and a second one in the other direction adds nothing.",
        "memoriesQL never clique-completes or transitively infers relations. A path "
        "through several relations is an investigation route, not a new assertion, and "
        "a containment or provenance hop never extends a causal claim.",
        "No temporal predicate exists; chronology stays in time and source-order "
        "facilities. No EXPRESS predicate is added only to fill that family.",
        "LEADSTO predicates connect statements describing occurrences, actions, "
        "decisions or changing states. A report or hypothesis about a cause is not "
        "itself the cause.",
        "An assertion that references itself is always refused. For a key whose cycles "
        "are forbidden (part_of, derived_from, supersedes), a write is refused when that "
        "key's active assertions, as they would stand once the write commits, would form "
        "a cycle between statements.",
    ),
    mappings=(
        RelationFamilyMapping(
            key="supports", family=None, orientation=None, mapping_status="native_unresolved",
            note="Upstream's support arrow (LT-1:114) means operational service support "
            "and is not a mapping.",
        ),
        RelationFamilyMapping(
            key="contradicts", family=None, orientation=None,
            mapping_status="native_unresolved",
            note="Upstream contrast and opposition arrows do not express incompatibility.",
        ),
        RelationFamilyMapping(
            key="caused_by", family="LEADSTO", orientation=-1, mapping_status="established",
            upstream_anchor="causes / cause-by, LT-1:51",
        ),
        RelationFamilyMapping(
            key="led_to", family="LEADSTO", orientation=1, mapping_status="provisional",
            upstream_anchor="result / result-of, LT-1:60",
            note="Upstream's generic leads-to also means plain sequence, so the anchor is "
            "results-in.",
        ),
        RelationFamilyMapping(
            key="enables", family="LEADSTO", orientation=1, mapping_status="established",
            upstream_anchor="enables / enabled-by, LT-1:66",
        ),
        RelationFamilyMapping(
            key="part_of", family="CONTAINS", orientation=-2, mapping_status="established",
            upstream_anchor="has-pt / pt-of, CN-2:18",
        ),
        RelationFamilyMapping(
            key="depends_on", family="LEADSTO", orientation=-1, mapping_status="provisional",
            upstream_anchor="is prereq / has prereq, LT-1:79",
            note="Upstream's depends-on is the inverse of an operational sustains arrow, so "
            "it is not the anchor.",
        ),
        RelationFamilyMapping(
            key="blocks", family="LEADSTO", orientation=1,
            mapping_status="provisional_local_extension",
            note="No upstream equivalent; nearest constr, LT-1:96. Upstream models "
            "inhibition as negated arrows, so this is a local LEADSTO extension.",
        ),
        RelationFamilyMapping(
            key="derived_from", family=None, orientation=None, mapping_status="unresolved",
            upstream_anchor="derive-fr, LT-1:18; source, EP-3:47",
            note="Unresolved between a process derivation (derive-fr, LEADSTO -1) and a "
            "source attribution (source, EXPRESS +3); the choice depends on what the "
            "endpoints represent.",
        ),
        RelationFamilyMapping(
            key="supersedes", family=None, orientation=None,
            mapping_status="intentionally_native",
            note="Upstream repl (LT-1:65) runs new to old inside LEADSTO, which is the "
            "flattening this profile avoids.",
        ),
        RelationFamilyMapping(
            key="associated_with", family="NEAR", orientation=0, mapping_status="established",
            upstream_anchor="ass, NR-0:47",
            note="Upstream clique-completes its NEAR arrows; memoriesQL does not.",
        ),
    ),
    attribution=(
        "The Semantic Spacetime family codes, readings and arrow aliases come from Mark "
        "Burgess's SSTorytime (Apache-2.0) at commit "
        f"{SSTORYTIME_COMMIT}, and from Burgess, Agent Semantics, Semantic Spacetime, "
        "and Graphical Reasoning, arXiv:2506.07756v2 sections 2.2-2.3. The upstream name "
        "of the third family is EXPRESS (configuration keyword properties). memoriesQL's "
        "definitions are its own; no SSTorytime code or configuration is copied or "
        "depended on."
    ),
)
