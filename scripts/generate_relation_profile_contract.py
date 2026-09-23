"""Generate the explicit relation semantic profile record; no discovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import relation_profile as rp

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-relation-profile-v1.json"


def record() -> dict[str, object]:
    models = (rp.FamilyCode, rp.RelationFamilyMapping, rp.RelationSemanticProfile)
    return dict(
        id="memoriesql.relation-profile.v1", version="1", kind="json_schema",
        status="available",
        summary="Relation semantic profile revision 1: the Semantic Spacetime family projection over the eleven built-in relation predicates, with mapping status, upstream anchors and attribution; it never gates authoring, acceptance or truth.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            profile=rp.RELATION_SEMANTIC_PROFILE.model_dump(mode="json"),
            layers="Predicate: the registered relation type revision with its exact meaning. Family: navigation and presentation only. Basis: each assertion's own propositions, evidence and qualification.",
            versioning="A mapping change is a new profile revision and never a meaning revision; stored assertions keep their relation type revision pins.",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("relation profile contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
