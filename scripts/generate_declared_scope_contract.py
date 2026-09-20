"""Generate the explicit declared-scope contract; published records stay unchanged."""

import argparse
import json
from pathlib import Path

from memoriesql.application.declared_evidence_scope import (
    DeclaredScopeMaterializationReceipt,
    InspectScopedEvidencePackage,
    MaterializeDeclaredScope,
    ScopedEvidencePackageStatus,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = (
        Path(__file__).resolve().parents[1]
        / "contracts/records/memoriesql-declared-evidence-scope-v1.json"
    )
    record = {
        "id": "memoriesql.declared-evidence-scope.v1",
        "version": "1",
        "kind": "json_schema",
        "status": "available",
        "summary": "Explicit authorized source-local evidence scopes with stable first bindings, truthful coverage and no invented native topology.",
        "contract": {
            "schemas": {
                m.__name__: m.model_json_schema()
                for m in (
                    MaterializeDeclaredScope,
                    DeclaredScopeMaterializationReceipt,
                    InspectScopedEvidencePackage,
                    ScopedEvidencePackageStatus,
                )
            },
            "package_contract": "memoriesql.evidence-package.v1",
            "native_boundary": "unresolved",
            "episode_completeness": "unknown",
            "semantic_sufficiency": "authored_not_mechanically_proven",
            "identity_scope": [
                "tenant",
                "source_object",
                "approved_namespace",
                "scope_id",
            ],
            "replay": "reuse_persisted_scope_declaration_and_original_pins",
            "cross_revision": "explicit_qualified_carry_forward_and_unchanged_content_required",
            "overlap": "not_identity_or_automatic_supersession",
        },
    }
    expected = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.check:
        if path.read_text() != expected:
            raise SystemExit("declared-scope contract drift")
    else:
        path.write_text(expected)


if __name__ == "__main__":
    main()
