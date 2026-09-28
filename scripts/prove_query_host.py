"""Installed acceptance of the trusted query host, outside the confined runner.

The host needs Unix sockets and separate host processes (restart, SIGKILL
mid-query), which `run_installed_acceptance.py` denies by design. Invoke with
`python -I` from the disposable directory holding the flat-copied runtime tests,
after installing the wheel; it uses the same N1_TEST_DATABASE_URL service and
fictional fixtures. No owner data, model call or production database is involved.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main() -> int:
    sys.path.insert(0, str(Path.cwd()))
    suite = unittest.defaultTestLoader.loadTestsFromName("query_host_acceptance")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        # A skipped database case is not proof of anything.
        print("query host acceptance skipped cases; refusing to pass", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
