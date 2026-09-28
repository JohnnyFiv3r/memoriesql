"""Test-only fault injection for trusted query host process death; never shipped.

The host runs normally, except that its restricted reader connection pauses at
the moment an admitted SELECT is about to be sent: it publishes its backend pid
to `<gates>/reader.pid` and waits for `<gates>/reader.go`. The test uses that
window to freeze the reader backend and then kill the whole host process, so a
query is genuinely still in PostgreSQL when its owner disappears.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import psycopg

from memoriesql.infrastructure.results_broker import server

GATE_SECONDS = 60.0


def _wait(gate: Path) -> None:
    deadline = time.monotonic() + GATE_SECONDS
    while not gate.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("fault gate was never opened")
        time.sleep(0.01)


def main(config_path: str, gates: str) -> int:
    directory = Path(gates)

    class PausingReader(psycopg.Connection[Any]):
        def cursor(self, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("name"):
                (directory / "reader.pid").write_text(str(self.info.backend_pid))
                _wait(directory / "reader.go")
            return super().cursor(*args, **kwargs)

    def reader(host: server.TrustedHost) -> psycopg.Connection[Any]:
        return PausingReader.connect(host.config.reader_conninfo(), autocommit=True)

    setattr(server.TrustedHost, "reader", reader)
    return server.serve(Path(config_path))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
