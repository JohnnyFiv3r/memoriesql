"""Local trusted query host for `memoriesql.agent-sql-results.v1`.

The host is one process, running as a dedicated service user, that holds both
database connection classes (trusted bookkeeping and the restricted reader).
Agents reach it only through a peer-authenticated Unix socket and present their
own paired credential; they never receive a database credential or client.
Frames carry the executor's closed request and reply bytes unchanged.
"""

from __future__ import annotations

PROTOCOL_VERSION = 1
