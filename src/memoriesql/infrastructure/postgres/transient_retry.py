"""Bounded re-run of a short owned transaction that lock contention stopped.

Kernel functions and adapters wait at most 500 ms for a lock. A transaction
that timed out there (SQLSTATE 55P03), lost a deadlock (40P01) or met
PostgreSQL's own serialization failure (40001, "could not serialize access")
has rolled back and written nothing, so the caller that owns it may run it
again. A 40001 that a kernel function raises itself, such as a stale revision,
is a real conflict and is never retried; nor is any other error, including a
statement timeout (57014). After the last attempt the original error is raised
unchanged, so every caller maps it exactly as before.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from psycopg import errors

# At most four runs of a 500 ms lock wait plus 0.35 s of backoff: an owner
# command or a read still ends within about 2.4 s of lock waiting.
ATTEMPTS = 4
FIRST_BACKOFF_SECONDS = 0.05
MAX_BACKOFF_SECONDS = 0.2
SERVER_SERIALIZATION = "could not serialize access"
# Indirections a test may replace; production waits in real time.
pause: Callable[[float], None] = time.sleep
jitter: Callable[[], float] = random.random


def transient(error: BaseException) -> bool:
    """Whether `error` is lock contention that rolled back an owned transaction."""
    if isinstance(error, errors.LockNotAvailable | errors.DeadlockDetected):
        return True
    if isinstance(error, errors.SerializationFailure):
        message = error.diag.message_primary or str(error)
        return message.startswith(SERVER_SERIALIZATION)
    return False


def run_owned[ResultT](
    attempt: Callable[[], ResultT], *, attempts: int = ATTEMPTS
) -> ResultT:
    """Run `attempt`, which opens and ends its own transaction, up to `attempts` times.

    Backoff doubles from 50 ms to at most 200 ms. Each wait keeps at least half
    its length and adds random jitter for the rest, so callers released by the
    same lock holder do not collide again.
    """
    if type(attempts) is not int or not 1 <= attempts <= ATTEMPTS:
        raise ValueError("invalid attempt count")
    backoff = FIRST_BACKOFF_SECONDS
    for remaining in range(attempts - 1, -1, -1):
        try:
            return attempt()
        except errors.Error as error:
            if remaining == 0 or not transient(error):
                raise
        pause(backoff / 2 + backoff / 2 * jitter())
        backoff = min(MAX_BACKOFF_SECONDS, backoff * 2)
    raise AssertionError("unreachable")
