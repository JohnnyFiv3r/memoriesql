"""Run copied public tests with filesystem access confined to the test environment.

Invoke with python -I from the disposable directory, after installing the wheel.
No checkout paths are accepted or searched. This script itself must be copied.
"""

from __future__ import annotations

import mimetypes
import os
import sys
import tempfile
import unittest
from pathlib import Path


def main() -> int:
    temporary = Path.cwd() / "temporary"
    temporary.mkdir(exist_ok=True)
    tempfile.tempdir = str(temporary)
    allowed = tuple(
        Path(p).resolve() for p in (sys.prefix, sys.base_prefix, os.getcwd())
    )

    # PydanticAI reads standard OS MIME databases during import. Permit only
    # this explicit stdlib list; checkouts and user-home resources remain denied.
    system_mime_files = frozenset(Path(p).resolve() for p in mimetypes.knownfiles)

    def guard(event: str, args: tuple[object, ...]) -> None:
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
                raise PermissionError("installed acceptance denied external network")
        if event == "subprocess.Popen":
            raise PermissionError("installed acceptance denied subprocess")
        if event in {"open", "os.listdir", "os.scandir"} and args:
            raw = args[0]
            if isinstance(raw, str | bytes | os.PathLike):
                path = Path(os.fsdecode(raw)).resolve()
                if path not in system_mime_files and not any(
                    path.is_relative_to(root) for root in allowed
                ):
                    raise PermissionError(
                        "installed acceptance denied external filesystem access"
                    )

    sys.addaudithook(guard)
    # Ensure guard is live without probing any real external file.
    try:
        Path("/n1-unavailable-checkout/sentinel").read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError("filesystem isolation guard is inactive")
    suite = unittest.defaultTestLoader.discover(".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
