"""Test-only fault injection for trusted query host process death; never shipped.

The host runs normally, except at one gated point:

- `reader`: its restricted reader connection pauses at the moment an admitted
  SELECT is about to be sent. It publishes its backend pid to
  `<gates>/reader.pid` and waits for `<gates>/reader.go`. The test uses that
  window to freeze the reader backend and then kill or orphan the host, so a
  query is genuinely still in PostgreSQL when its owner disappears.
- `commit`: the reader has finished and settled, and the host pauses just
  before committing the result. It writes `<gates>/commit.ready` and waits for
  `<gates>/commit.go`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import psycopg

from memoriesql.infrastructure.postgres.query_result_commit import (
    PostgresQueryResultCommit,
)
from memoriesql.infrastructure.results_broker import server

GATE_SECONDS = 60.0


def _wait(gate: Path) -> None:
    deadline = time.monotonic() + GATE_SECONDS
    while not gate.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("fault gate was never opened")
        time.sleep(0.01)


def main(config_path: str, gates: str, point: str = "reader") -> int:
    directory = Path(gates)
    if point == "reader":

        class PausingReader(psycopg.Connection[Any]):
            def cursor(self, *args: Any, **kwargs: Any) -> Any:
                if kwargs.get("name"):
                    pid = str(self.info.backend_pid)
                    (directory / "reader.pid").write_text(pid)
                    _wait(directory / "reader.go")
                return super().cursor(*args, **kwargs)

        def reader(host: server.TrustedHost) -> psycopg.Connection[Any]:
            conninfo = host.config.reader_conninfo()
            return PausingReader.connect(conninfo, autocommit=True)

        setattr(server.TrustedHost, "reader", reader)
    elif point == "commit":
        commit = PostgresQueryResultCommit.commit

        def gated(self: PostgresQueryResultCommit, *args: Any, **kwargs: Any) -> Any:
            (directory / "commit.ready").write_text("ready")
            _wait(directory / "commit.go")
            return commit(self, *args, **kwargs)

        setattr(PostgresQueryResultCommit, "commit", gated)
    else:
        raise ValueError("unknown fault point")
    return server.serve(Path(config_path))


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
