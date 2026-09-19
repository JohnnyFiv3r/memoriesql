"""Generate the explicit forward local-mention record; no discovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import local_entity_mentions as lm
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-local-entity-mentions-v1.json"


def record() -> dict[str, object]:
    registry = lm.load_local_mentions_task_registry(
        BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
    )
    models = (lm.LocalEntityMention, lm.MentionBeadDraft, lm.MentionExecutionOutput,
              lm.MentionExecutionInput, lm.MentionAuthorStep, lm.ActivateMentionAuthorship,
              lm.MentionActivationReceipt, lm.ApplyLocalMentions)
    return dict(
        id="memoriesql.local-entity-mentions.v1", version="1", kind="json_schema",
        status="available",
        summary="Atomic authored local mentions through revision-4 source revisiting; no global identity resolution.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            task_definition=lm.MENTION_EXECUTION_TASK.canonical_payload(),
            task_contract_hash=lm.MENTION_EXECUTION_TASK.contract_hash,
            registry_hash=registry.registry_hash,
            evidence="Enclosing immutable bead/source unit and original task/package pin; no fabricated character offsets or statement links.",
            empty_set="Required mentions array may be empty. Accepted bead version's own task receipt (author-complete-unit revision 4) proves capability; legacy or unavailable results do not imply empty.",
            uncertainty="Immutable local unresolved/ambiguous state is separate from later governed entity resolution; no canonical candidates invented.",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("local mentions contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
