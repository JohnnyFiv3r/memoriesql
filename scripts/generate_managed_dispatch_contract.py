"""Generate the forward supervised-dispatch contract without changing v1 records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application.managed_dispatch import (
    ManagedDispatchIntent,
    ManagedUsageEvent,
    SupervisedQualification,
    SupervisedRequestIntent,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = (
        Path(__file__).resolve().parents[1]
        / "contracts/records/memoriesql-supervised-dispatch-v1.json"
    )
    record = {
        "id": "memoriesql.supervised-dispatch.v1",
        "version": "1",
        "kind": "json_schema",
        "status": "available",
        "summary": "Explicit task-bound supervised qualification of one observable managed turn and an optional hard-bounded follow-up; no hidden-inference or remote-ceiling claims.",
        "contract": {
            "schemas": {
                m.__name__: m.model_json_schema()
                for m in (
                    SupervisedQualification,
                    ManagedDispatchIntent,
                    SupervisedRequestIntent,
                    ManagedUsageEvent,
                )
            },
            "default_admission": "unchanged_hard_bounded_single_inference",
            "allowance": "durable_pre_dispatch_serial_nonrefundable_across_attempts",
            "managed_usage": "reported_total_else_complete_request_observations_else_estimate_else_unavailable",
            "counting": "observable_turn_not_underlying_inference",
            "exposure": "host_visible_supply_only",
            "stop_triggers": "reported_usage_not_hard_remote_token_cash_or_termination_guarantees",
            "qualification": "separate_explicit_task_route_model_authority_deadline_approval",
        },
    }
    expected = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.check:
        if path.read_text() != expected:
            raise SystemExit("supervised-dispatch contract drift")
    else:
        path.write_text(expected)


if __name__ == "__main__":
    main()
