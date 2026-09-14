"""Compare unreleased recovery archives; never alter the published release gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.verify_release import verify_artifacts
else:
    from verify_release import verify_artifacts

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads(
        (root / "docs/verification/fold-recovery-candidate-artifacts.json").read_text()
    )
    if inventory["release_status"] != "unreleased-no-version-selected":
        raise SystemExit("not an unreleased recovery candidate")
    print(
        json.dumps(
            {
                "candidate_archives": [
                    p.name for p in verify_artifacts(Path(sys.argv[1]), inventory)
                ]
            }
        )
    )
