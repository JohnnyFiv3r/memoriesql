"""Run copied public tests with filesystem access confined to the test environment.

Invoke with python -I from the disposable directory, after installing the wheel.
No checkout paths are accepted or searched. This script itself must be copied.

The discovered test modules can be split into shards (`--shard 2/4` runs the
second of four) so several interpreter processes share one PostgreSQL service;
every module runs in exactly one shard. Modules are placed longest-first by the
seconds recorded in `acceptance-shard-weights.json` beside this script (a module
missing there is estimated from its test count), and each run prints the seconds
every module took; `--print-weights` prints them in that file's format.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import time
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    _TextTestResult = unittest.TextTestResult[Any]
else:
    _TextTestResult = unittest.TextTestResult

WEIGHTS = Path("acceptance-shard-weights.json")
# Seconds a test is assumed to take when its module has no recorded weight.
UNWEIGHTED_SECONDS_PER_TEST = 1.0


def install_guard() -> None:
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


def discovered_modules() -> dict[str, int]:
    """Discovered test modules, in discovery order, with their test counts."""
    modules: dict[str, int] = {}
    broken: list[str] = []

    def walk(item: unittest.TestSuite | unittest.TestCase) -> None:
        if isinstance(item, unittest.TestSuite):
            for child in item:
                walk(child)
            return
        module = type(item).__module__
        if module == "unittest.loader":
            broken.append(str(item))
            return
        modules[module] = modules.get(module, 0) + 1

    walk(unittest.defaultTestLoader.discover("."))
    if broken:
        raise SystemExit("test discovery failed:\n" + "\n".join(broken))
    return modules


def recorded_weights() -> dict[str, float]:
    if not WEIGHTS.is_file():
        return {}
    seconds = json.loads(WEIGHTS.read_text()).get("seconds")
    if not isinstance(seconds, dict):
        raise SystemExit(f"{WEIGHTS} must map 'seconds' to module durations")
    return {str(module): float(value) for module, value in seconds.items()}


def shard_modules(
    modules: dict[str, int], weights: dict[str, float], index: int, count: int
) -> list[str]:
    """Longest-processing-time placement; the chosen shard in discovery order."""

    def weight(module: str) -> float:
        return weights.get(module, modules[module] * UNWEIGHTED_SECONDS_PER_TEST)

    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0.0] * count
    for module in sorted(modules, key=lambda name: (-weight(name), name)):
        position = loads.index(min(loads))
        buckets[position].append(module)
        loads[position] += weight(module)
    order = {module: place for place, module in enumerate(modules)}
    return sorted(buckets[index - 1], key=order.__getitem__)


def parse_shard(value: str) -> tuple[int, int]:
    index_text, _, count_text = value.partition("/")
    try:
        index, count = int(index_text), int(count_text)
    except ValueError:
        raise argparse.ArgumentTypeError("--shard takes INDEX/COUNT") from None
    if count < 1 or not 1 <= index <= count:
        raise argparse.ArgumentTypeError("--shard needs 1 <= INDEX <= COUNT")
    return index, count


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shard", type=parse_shard, metavar="INDEX/COUNT")
    parser.add_argument("--print-weights", action="store_true")
    args = parser.parse_args(arguments)
    install_guard()
    modules = discovered_modules()
    if args.shard is None:
        selected = list(modules)
    else:
        index, count = args.shard
        selected = shard_modules(modules, recorded_weights(), index, count)
        print(f"shard {index}/{count}: {len(selected)} of {len(modules)} modules")
    seconds: dict[str, float] = {}

    class TimedResult(_TextTestResult):
        def startTest(self, test: unittest.TestCase) -> None:
            self._started = time.perf_counter()
            super().startTest(test)

        def stopTest(self, test: unittest.TestCase) -> None:
            super().stopTest(test)
            module = type(test).__module__
            seconds[module] = seconds.get(module, 0.0) + (
                time.perf_counter() - self._started
            )

    suite = unittest.defaultTestLoader.loadTestsFromNames(selected)
    result = unittest.TextTestRunner(verbosity=2, resultclass=TimedResult).run(suite)
    for module, elapsed in sorted(seconds.items(), key=lambda item: -item[1]):
        print(f"{elapsed:7.1f}s {module}")
    if args.print_weights:
        print(
            json.dumps(
                {"seconds": {m: round(v, 1) for m, v in sorted(seconds.items())}},
                indent=2,
            )
        )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
