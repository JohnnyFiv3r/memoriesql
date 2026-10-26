"""Run one part of the installed fictional proof, or aggregate every part.

Each part retains every child outcome; only the aggregation of all parts of an
interpreter can complete its proof.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import platform
import pwd
import re
import resource
import secrets
import shutil
import signal
import stat
import string
import struct
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

POSTGRES = (
    "postgres@sha256:a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636"
)
RESTARTS = (
    "prove_assessed_relation_restart.py",
    "prove_fold_recovery_restart.py",
    "prove_database_time_recovery_restart.py",
    "prove_query_result_restart.py",
)
SCRIPTS = (
    "run_installed_acceptance.py",
    "prove_package_isolation.py",
    "prove_release_installation.py",
    "prove_query_host.py",
    *RESTARTS,
)
RUNTIME_DEPS = (
    "psycopg[binary]==3.3.3",
    "pydantic==2.13.3",
    "pydantic-ai-slim==2.27.0",
    "sqlglot==30.19.0",
)
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_COMMANDS = 40
# Acceptance is split into parts, each on its own runner with its own PostgreSQL
# service: four processes on one four-CPU runner saturated it (F-3). Two shards per
# part stay below the three-process load the shard weights were recorded under.
ACCEPTANCE_PARTS = 3
SHARDS_PER_PART = 2
# One longest-first plan over every shard of the interpreter; part p runs its slice.
ACCEPTANCE_SHARDS = ACCEPTANCE_PARTS * SHARDS_PER_PART
# The restart triplets and the host proof run once per interpreter, in this part.
HOST_PROOF_PART = 1
REPOSITORY = "JohnnyFiv3r/memoriesql"
# A part's record; only the aggregation writes qualification.json.
PART_RECORD = "qualification-part.json"
# Every part runs these, in order, before its own acceptance shards.
SETUP_COMMANDS = (
    "verify-source",
    "source-head",
    "verify-artifacts",
    "dependencies",
    "build-tools",
    "sdist-wheel",
    "venv",
    "install-wheel",
    "tracked-inputs",
    "installed-environment",
    "installed-import-roots",
    "installed-mapped-objects",
    "package-isolation",
    "release-installation",
)
# Then the host part alone runs these, in order.
HOST_COMMANDS = (
    *(
        f"restart-{index}-{step}"
        for index in range(1, len(RESTARTS) + 1)
        for step in ("prepare", "recover", "cleanup")
    ),
    "query-host-facts",
    "query-host",
)
# Interpreter identity that does not depend on a part's private relocation path.
INTERPRETER_IDENTITY = (
    "build",
    "version",
    "architecture",
    "source_python_sha256",
    "source_tree_sha256",
    "normalized_copy_tree_sha256",
)
# The aggregation recomputes the shard plan from these exact-source files.
PLAN_INPUTS = (
    "scripts/run_installed_acceptance.py",
    "tests/fixtures/acceptance-shard-weights.json",
)
# Runner load is observed, never enforced: CPU, memory and pressure every 15 s.
RESOURCE_SAMPLE_SECONDS = 15
HOST_AUTHORITY_SHA256 = (
    "83e3a3d63a1a8a9da88d1a0688e9241527726dd856be33a0b039a735cb5b504c"
)
HOST_ENV = (
    "N1_TEST_CLI_IDENTITY_BINDING",
    "N1_TEST_CLI_IDENTITY_BINDING_SHA256",
    "N1_TEST_CLI_IDENTITY_AUTHORITY_SHA256",
)
MAX_HOST_BINDING = 65536
# Executed once by the installed interpreter, without run-as or product imports.
HOST_FACTS_PROBE = r"""
import base64, hashlib, importlib.metadata, json, os, pwd, stat, sys
from pathlib import Path
def pin(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
distribution = importlib.metadata.distribution("memoriesql")
files = distribution.files
if not files:
    raise ValueError("missing installed RECORD")
installed = []
for entry in files:
    path = Path(distribution.locate_file(entry)).absolute()
    if entry.hash is not None:
        if entry.hash.mode != "sha256" or path.is_symlink():
            raise ValueError("unbound installed file")
        actual = base64.urlsafe_b64encode(bytes.fromhex(pin(path))).rstrip(b"=").decode()
        if actual != entry.hash.value or path.stat().st_size != entry.size:
            raise ValueError("installed RECORD mismatch")
    elif str(entry).endswith(".pyc") or str(entry).endswith(".dist-info/RECORD"):
        continue
    else:
        raise ValueError("unhashed installed input")
    if str(entry).startswith("memoriesql/"):
        installed.append([str(entry), str(path), pin(path)])
if not installed:
    raise ValueError("missing installed product inputs")
system = {}
for name in ("sudo", "env", "setpriv"):
    path = Path("/usr/bin") / name
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
        raise ValueError("unbound system launcher")
    system[name + "_sha256"] = pin(path)
print(json.dumps({
    "service_uid": os.geteuid(), "client_uid": pwd.getpwnam("nobody").pw_uid,
    "gid": os.getegid(), "groups": [os.getegid()],
    "python": str(Path(sys.executable).absolute()),
    "python_resolved": str(Path(sys.executable).resolve()),
    "python_sha256": pin(Path(sys.executable).resolve()),
    "helper_sha256": pin(Path("query_host_cli_identity.py")),
    "import_roots": list(sys.path), "installed": installed, **system,
}, sort_keys=True, separators=(",", ":")))
"""


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_file_limit() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))


class Commands:
    def __init__(self, evidence: Path, seconds: int = 5400) -> None:
        self.evidence = evidence
        self.deadline = time.monotonic() + seconds
        self.cleanup_deadline = self.deadline + 120
        self.rows: list[dict[str, Any]] = []
        self.active: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []

    def save(self) -> None:
        temporary = self.evidence / "commands.json.tmp"
        temporary.write_text(json.dumps(self.rows, indent=2) + "\n")
        temporary.replace(self.evidence / "commands.json")

    def start(
        self,
        name: str,
        args: list[str],
        cwd: Path,
        *,
        env: dict[str, str] | None = None,
    ) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
        if not re.fullmatch("[a-z0-9-]+", name) or len(self.rows) >= MAX_COMMANDS:
            raise ValueError("invalid or excessive qualification command")
        if env is not None and set(env) != set(HOST_ENV):
            raise ValueError("unexpected host child environment")
        log = self.evidence / (name + ".log")
        row: dict[str, Any] = {
            "name": name,
            "argv": args,
            "started": time.time(),
            "exit_code": None,
            "complete": False,
            "log": log.name,
        }
        self.rows.append(row)
        self.save()
        with log.open("xb") as stream:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=child_file_limit,
                env={
                    **{k: v for k, v in os.environ.items() if k not in HOST_ENV},
                    **(env or {}),
                    "N1_QUALIFICATION_DEADLINE": repr(self.deadline),
                    "N1_QUALIFICATION_CLEANUP_DEADLINE": repr(self.cleanup_deadline),
                },
            )
        row["pid"] = process.pid
        self.active.append((process, row))
        self.save()
        return process, row

    def finish(
        self,
        child: tuple[subprocess.Popen[bytes], dict[str, Any]],
        *,
        cleanup: bool = False,
    ) -> int:
        process, row = child
        try:
            deadline = self.cleanup_deadline if cleanup else self.deadline
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            row["deadline_exhausted"] = True
            self.stop(process)
        row.update(exit_code=process.returncode, complete=True, finished=time.time())
        log = self.evidence / row["log"]
        row.update(log_sha256=file_hash(log), log_bytes=log.stat().st_size)
        self.active.remove(child)
        self.save()
        if row.get("deadline_exhausted") or row["log_bytes"] >= MAX_LOG_BYTES:
            return 1
        return int(process.returncode) if process.returncode is not None else 1

    @staticmethod
    def stop(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def run(
        self,
        name: str,
        args: list[str],
        cwd: Path,
        *,
        cleanup: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        if self.finish(self.start(name, args, cwd, env=env), cleanup=cleanup):
            raise RuntimeError("qualification command failed: " + name)

    def capture(self, name: str, args: list[str], cwd: Path) -> str:
        self.run(name, args, cwd)
        return (self.evidence / (name + ".log")).read_text()

    def close(self) -> None:
        for child in list(self.active):
            self.stop(child[0])
            self.finish(child)


def prepare_host_binding(
    runner: Commands, test_root: Path, python: str, sha: str, wheel: Path
) -> dict[str, str]:
    """Seal observed inputs; independent job admission supplies the authority pin.

    This prepares no privilege. The unchanged C worker still proves/refuses all
    effective-access, interpreter, path, identity and cleanup requirements.
    """
    authority = os.environ.get(HOST_ENV[2])
    if authority != HOST_AUTHORITY_SHA256:
        raise ValueError("independent host fixture authority missing or mismatched")
    if (
        os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("SOURCE_SHA") != sha
        or re.fullmatch("[0-9a-f]{40}", sha) is None
    ):
        raise ValueError("host fixture source or platform mismatch")
    raw = runner.capture(
        "query-host-facts", [python, "-I", "-c", HOST_FACTS_PROBE], test_root
    )
    if len(raw.encode()) > MAX_HOST_BINDING:
        raise ValueError("host facts exceed frame limit")
    facts = json.loads(raw)
    for key in ("service_uid", "client_uid", "gid"):
        if type(facts[key]) is not int or facts[key] <= 0:
            raise ValueError("host identities must be actual nonroot integers")
    if (
        facts["service_uid"] != os.geteuid()
        or facts["gid"] != os.getegid()
        or facts["client_uid"] == facts["service_uid"]
        or facts["groups"] != [facts["gid"]]
        or facts["python"] != python
        or facts["python_resolved"] != str(Path(python).resolve())
        or facts["python_sha256"] != file_hash(Path(python).resolve())
        or facts["helper_sha256"] != file_hash(test_root / "query_host_cli_identity.py")
    ):
        raise ValueError("host fixture identity or executable substitution")
    roots = facts["import_roots"]
    if (
        not isinstance(roots, list)
        or not roots
        or any(
            not isinstance(p, str)
            or not p
            or not Path(p).is_absolute()
            or ".." in Path(p).parts
            for p in roots
        )
    ):
        raise ValueError("incomplete absolute installed import roots")
    inputs = [
        ["@installed-python", facts["python_sha256"]],
        ["@copied-helper", facts["helper_sha256"]],
    ]
    installed = facts["installed"]
    if not isinstance(installed, list) or not installed:
        raise ValueError("missing installed inputs")
    with zipfile.ZipFile(wheel) as archive:
        expected = {
            n
            for n in archive.namelist()
            if n.startswith("memoriesql/") and not n.endswith("/")
        }
        if {entry[0] for entry in installed} != expected or len(installed) != len(
            expected
        ):
            raise ValueError("installed and reviewed wheel inputs differ")
        for name, path, pin in installed:
            if (
                not Path(path).is_absolute()
                or ".." in Path(path).parts
                or not any(Path(path).is_relative_to(Path(root)) for root in roots)
                or file_hash(Path(path)) != pin
                or hashlib.sha256(archive.read(name)).hexdigest() != pin
            ):
                raise ValueError("installed wheel input substitution")
            inputs.append([path, pin])
    for key in ("sudo_sha256", "env_sha256", "setpriv_sha256"):
        name = key.removesuffix("_sha256")
        path = Path("/usr/bin") / name
        info = path.lstat()
        if (
            info.st_uid != 0
            or info.st_mode & 0o022
            or not stat.S_ISREG(info.st_mode)
            or file_hash(path) != facts[key]
        ):
            raise ValueError("system launcher substitution")
    plan = {
        **{
            key: facts[key]
            for key in (
                "service_uid",
                "client_uid",
                "gid",
                "groups",
                "import_roots",
                "sudo_sha256",
                "env_sha256",
                "setpriv_sha256",
            )
        },
        "authority_sha256": authority,
        "source_sha": sha,
        "root": "@fresh-fixture",
        "python": "@installed-python",
        "helper": "@copied-helper",
        "inputs": inputs,
    }
    data = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    if len(data) > MAX_HOST_BINDING:
        raise ValueError("host binding exceeds frame limit")
    path = test_root / "query-host-cli-binding.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        actual = os.read(fd, MAX_HOST_BINDING + 1)
    finally:
        os.close(fd)
    if (
        info.st_uid != plan["service_uid"]
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_mode & 0o077
        or actual != data
    ):
        raise ValueError("sealed host binding substituted")
    return {
        HOST_ENV[0]: str(path),
        HOST_ENV[1]: hashlib.sha256(data).hexdigest(),
        HOST_ENV[2]: HOST_AUTHORITY_SHA256,
    }


def identity_inventory(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        raise ValueError("missing installed test identity inventory")
    for module, cases in value.items():
        if not isinstance(module, str) or not isinstance(cases, dict) or not cases:
            raise ValueError("invalid installed test module inventory")
        if any(
            not isinstance(identity, str) or type(number) is not int or number < 1
            for identity, number in cases.items()
        ):
            raise ValueError("invalid installed test identity multiplicity")
    return value


def client_writable(info: os.stat_result, gid: int) -> bool:
    """By mode, may the run-as client (sole group: the job's own) write here?"""
    return bool(
        info.st_mode & stat.S_IWOTH
        or (info.st_mode & stat.S_IWGRP and info.st_gid == gid)
    )


def check_client_unwritable(
    python: str,
    helper: Path,
    roots: list[str],
    client_uid: int,
    gid: int,
    mapped: tuple[str, ...] = (),
    bound: tuple[Path, ...] = (),
) -> None:
    """Fail fast, before the long acceptance, on inputs the run-as worker refuses.

    From S's side, mirror the worker's rule for the interpreter, the copied
    helper and every import root: no path, ancestor or link target may be
    client-owned or client-writable, except a sticky non-client directory over a
    non-client child, and import roots hold no symlinks. Every object the
    installed interpreter maps must lie in its bound trees or in root-owned,
    non-writable system directories. Refusals name a fixed category and the
    entry's numeric mode/uid/gid, never a path; the worker's own check stays
    authoritative.
    """
    passed: dict[Path, os.stat_result] = {}  # neither client-owned nor -writable

    def refuse(reason: str, category: str, info: os.stat_result) -> NoReturn:
        raise ValueError(
            f"{reason}: category={category} mode={oct(info.st_mode)} "
            f"uid={info.st_uid} gid={info.st_gid}"
        )

    def check(
        path: Path, kind: str, allow_missing: bool = False, depth: int = 0
    ) -> None:
        if depth >= 40:
            raise ValueError("bound input links do not resolve: category=link")
        child: os.stat_result | None = None
        for entry in (path, *path.parents):
            category = "link" if depth else kind if entry == path else "ancestor"
            if entry in passed:
                child = passed[entry]
                continue
            try:
                info = entry.lstat()
            except FileNotFoundError:
                if not allow_missing:
                    raise ValueError(
                        f"bound input missing: category={category}"
                    ) from None
                child = None
                continue
            if info.st_uid == client_uid:
                refuse("bound input is client-owned", category, info)
            if stat.S_ISLNK(info.st_mode):
                target = Path(os.readlink(entry))
                check(
                    target if target.is_absolute() else entry.parent / target,
                    kind,
                    allow_missing,
                    depth + 1,
                )
            elif not client_writable(info, gid):
                passed[entry] = info
            elif not (
                entry != path
                and stat.S_ISDIR(info.st_mode)
                and info.st_mode & stat.S_ISVTX
                and child is not None
                and child.st_uid != client_uid
            ):
                refuse("bound input is client-writable", category, info)
            child = info

    check(Path(python), "interpreter")
    check(helper, "helper")
    for raw in roots:
        root = Path(raw)
        check(root, "import root", allow_missing=True)
        if not root.is_dir():
            if root.is_symlink():
                refuse("import root holds a symlink", "import root", root.lstat())
            continue
        for base, directories, files in os.walk(root):
            for name in (*directories, *files):
                entry = Path(base) / name
                if entry.is_symlink():
                    refuse("import root holds a symlink", "import root", entry.lstat())
                check(entry, "import root")
    trees = [Path(raw) for raw in roots] + list(bound)
    for raw in mapped:
        path = Path(raw)
        if any(path == tree or tree in path.parents for tree in trees):
            check(path, "library")
            continue
        for entry in (path, *path.parents):
            try:
                info = entry.lstat()
            except FileNotFoundError:
                raise ValueError(
                    "interpreter maps a missing object: category=library"
                ) from None
            if (
                info.st_uid != 0
                or stat.S_ISLNK(info.st_mode)
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                refuse("interpreter maps an unbound object", "library", info)


# A setup-python build lives at <prefix>/Python/<version>/<arch>, and its ELF
# objects carry DT_RUNPATH <that>/lib. The private copy sits under a fresh
# prefix of the same byte length, so each rewrite replaces exactly that prefix.
DT_STRING_TAGS = (
    1,
    14,
    15,
    29,
    0x6FFFFEFA,
    0x6FFFFEFB,
    0x6FFFFEFC,
    0x7FFFFFFD,
    0x7FFFFFFF,
)
DT_SEARCH_TAGS = (15, 29)  # DT_RPATH, DT_RUNPATH
MAPS_PROBE = (
    "import hashlib,json,math,os,select,shutil,signal,stat,subprocess,sys,time,uuid;"
    "print(json.dumps(sorted({f[5].strip() for f in "
    "(l.split(None,5) for l in open('/proc/self/maps')) "
    "if len(f)==6 and f[5].startswith('/')})))"
)


class DynamicView:
    """The dynamic segment of an ELF64 little-endian object (no section headers)."""

    def __init__(self, data: bytes) -> None:
        if data[4:6] != b"\x02\x01":
            raise ValueError("private interpreter holds an unsupported ELF object")
        self.data = data
        (phoff,) = struct.unpack_from("<Q", data, 0x20)
        phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
        self.loads: list[tuple[int, int, int]] = []
        dynamic: tuple[int, int] | None = None
        for index in range(phnum):
            kind, _, offset, vaddr, _, filesz, _, _ = struct.unpack_from(
                "<IIQQQQQQ", data, phoff + index * phentsize
            )
            if kind == 1:
                self.loads.append((vaddr, offset, filesz))
            elif kind == 2:
                dynamic = (offset, filesz)
        self.entries: list[tuple[int, int]] = []
        if dynamic is not None:
            for offset in range(dynamic[0], dynamic[0] + dynamic[1], 16):
                tag, value = struct.unpack_from("<qQ", data, offset)
                if tag == 0:
                    break
                self.entries.append((tag, value))
        tags = dict(self.entries)
        self.strtab = self.offset(tags[5]) if 5 in tags else None
        self.strsz = tags.get(10, 0)

    def offset(self, vaddr: int) -> int:
        for start, offset, size in self.loads:
            if start <= vaddr < start + size:
                return vaddr - start + offset
        raise ValueError("private interpreter ELF address outside its file")

    def string(self, index: int) -> str:
        assert self.strtab is not None
        if not 0 <= index < self.strsz:
            raise ValueError("private interpreter ELF string outside its table")
        start = self.strtab + index
        return self.data[start : self.data.index(b"\0", start)].decode()

    def search_paths(self) -> list[tuple[int, str]]:
        if self.strtab is None:
            return []
        return [(v, self.string(v)) for tag, v in self.entries if tag in DT_SEARCH_TAGS]

    def symbol_count(self, tags: dict[int, int]) -> int:
        data = self.data
        if 4 in tags:  # DT_HASH: nchain is the symbol count
            return int(struct.unpack_from("<II", data, self.offset(tags[4]))[1])
        if 0x6FFFFEF5 not in tags:  # DT_GNU_HASH
            raise ValueError("private interpreter ELF symbols are unbounded")
        base = self.offset(tags[0x6FFFFEF5])
        nbuckets, symoffset, bloom, _ = struct.unpack_from("<IIII", data, base)
        buckets_at = base + 16 + bloom * 8
        last = max(struct.unpack_from(f"<{nbuckets}I", data, buckets_at), default=0)
        if last < symoffset:
            return int(symoffset)
        chains_at = buckets_at + nbuckets * 4
        while True:
            (value,) = struct.unpack_from(
                "<I", data, chains_at + (last - symoffset) * 4
            )
            last += 1
            if value & 1:
                return int(last)

    def references(self) -> list[int]:
        """Every dynamic string-table reference: tags, symbols, version strings."""
        data, tags = self.data, dict(self.entries)
        refs = [v for tag, v in self.entries if tag in DT_STRING_TAGS]
        if 6 in tags:  # DT_SYMTAB
            symtab, size = self.offset(tags[6]), tags.get(11, 24)
            for index in range(self.symbol_count(tags)):
                refs.append(struct.unpack_from("<I", data, symtab + index * size)[0])
        if 0x6FFFFFFC in tags:  # DT_VERDEF
            at = self.offset(tags[0x6FFFFFFC])
            for _ in range(tags.get(0x6FFFFFFD, 0)):
                _, _, _, count, _, aux, following = struct.unpack_from(
                    "<HHHHIII", data, at
                )
                entry = at + aux
                for _ in range(count):
                    name, step = struct.unpack_from("<II", data, entry)
                    refs.append(name)
                    entry += step
                at += following
        if 0x6FFFFFFE in tags:  # DT_VERNEED
            at = self.offset(tags[0x6FFFFFFE])
            for _ in range(tags.get(0x6FFFFFFF, 0)):
                _, count, file, aux, following = struct.unpack_from("<HHIII", data, at)
                refs.append(file)
                entry = at + aux
                for _ in range(count):
                    _, _, _, name, step = struct.unpack_from("<IHHII", data, entry)
                    refs.append(name)
                    entry += step
                at += following
        return refs


def relocate_search_path(
    data: bytearray, source_lib: str, new_prefix: bytes
) -> list[int]:
    """Rewrite each DT_RUNPATH/DT_RPATH equal to source_lib in place: exactly its
    first len(new_prefix) bytes, after proving no other string shares them."""
    view = DynamicView(bytes(data))
    targets = {v for v, value in view.search_paths() if value == source_lib}
    if not targets:
        return []
    assert view.strtab is not None
    width = len(new_prefix)
    refs = view.references()
    for tag, value in view.entries:
        if tag in DT_SEARCH_TAGS and value in targets:
            refs.remove(value)  # the rewritten search path's own entry
    for target in targets:
        low = view.strtab + target
        for ref in refs:
            if not 0 <= ref < view.strsz:
                raise ValueError("private interpreter ELF string outside its table")
            start = view.strtab + ref
            # Another string starting inside the range, or running into it,
            # would change with the rewrite; tails after the range are kept.
            if low <= start < low + width or (
                start < low and data.index(b"\0", start) > low
            ):
                raise ValueError("private interpreter search path shares its bytes")
    offsets = []
    old_prefix = source_lib.encode()[:width]
    for target in sorted(targets):
        at = view.strtab + target
        if bytes(data[at : at + width]) != old_prefix:
            raise ValueError("private interpreter search path prefix mismatch")
        data[at : at + width] = new_prefix
        offsets.append(at)
    return offsets


def private_root(prefix: str, parent: Path = Path("/tmp")) -> Path:
    """R1: a fresh, exclusive, S-owned 0755 directory of the prefix's byte length."""
    info = parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or not info.st_mode & stat.S_ISVTX
    ):
        raise ValueError("private root parent is not a root-owned sticky directory")
    stem = f"{parent}/mqh-"
    width = len(prefix.encode()) - len(stem.encode())
    if width < 8:
        raise ValueError("toolcache prefix too short for a private root")
    for _ in range(8):
        name = "".join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(width)
        )
        root = Path(stem + name)
        try:
            root.mkdir(mode=0o755)
        except FileExistsError:
            continue
        root.chmod(0o755)
        info = root.lstat()
        if (
            len(str(root).encode()) != len(prefix.encode())
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o755
        ):
            raise ValueError("private root is not exclusive, S-owned and 0755")
        return root
    raise ValueError("no fresh private root name")


def tree_digest(
    root: Path, reset: dict[str, list[int]] | None = None, prefix: bytes = b""
) -> str:
    """Sorted relative path, entry type, content sha256 or link text; no modes.

    With reset, each listed file's rewritten ranges are restored to prefix first.
    """
    digest = hashlib.sha256()
    for base, directories, files in os.walk(root):
        directories.sort()
        for name in sorted((*directories, *files)):
            entry = Path(base) / name
            relative = str(entry.relative_to(root))
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode):
                item = ["link", relative, os.readlink(entry)]
            elif stat.S_ISDIR(info.st_mode):
                item = ["dir", relative, ""]
            elif stat.S_ISREG(info.st_mode):
                data = bytearray(entry.read_bytes())
                for at in (reset or {}).get(relative, []):
                    data[at : at + len(prefix)] = prefix
                item = ["file", relative, hashlib.sha256(data).hexdigest()]
            else:
                raise ValueError("private interpreter has an unsupported entry")
            digest.update(json.dumps(item).encode() + b"\n")
    return digest.hexdigest()


def check_private_copy(copy: Path) -> None:
    """R4/R5: normalized S-owned modes, internal links, search paths inside the copy."""
    inside, real = str(copy), os.path.realpath(copy)
    for base, directories, files in os.walk(copy):
        for name in (*directories, *files):
            entry = Path(base) / name
            info = entry.lstat()
            if info.st_uid != os.geteuid():
                raise ValueError("private interpreter entry is not S-owned")
            if stat.S_ISLNK(info.st_mode):
                target = os.path.realpath(entry)
                if not (target.startswith(real + "/") and os.path.exists(target)):
                    raise ValueError("private interpreter link leaves the copy")
                continue
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                if mode != 0o755:
                    raise ValueError("private interpreter modes are not normalized")
                continue
            if mode not in (0o644, 0o755) or info.st_nlink != 1:
                raise ValueError("private interpreter modes are not normalized")
            data = entry.read_bytes()
            if data[:4] != b"\x7fELF":
                continue
            for _, value in DynamicView(data).search_paths():
                for part in value.split(":"):
                    if part.startswith(("$ORIGIN", "${ORIGIN}")):
                        tail = (
                            part.split("}", 1)[1] if part.startswith("${") else part[7:]
                        )
                        part = str(entry.parent) + tail
                    elif "$" in part or not part.startswith("/"):
                        raise ValueError(
                            "private interpreter search path leaves the copy"
                        )
                    resolved = os.path.normpath(part)
                    if not (resolved == inside or resolved.startswith(inside + "/")):
                        raise ValueError(
                            "private interpreter search path leaves the copy"
                        )


def private_interpreter(
    base: Path, parent: Path = Path("/tmp")
) -> tuple[Path, Path, dict[str, Any]]:
    """R1-R6: an S-owned copy of the setup-python build whose library search path
    is relocated into the copy by a verified same-length prefix rewrite."""
    if len(base.parts) < 4 or base.parts[-3] != "Python":
        raise ValueError("interpreter is not a toolcache layout")
    prefix = str(base.parents[2])
    root = private_root(prefix, parent)
    copy = root / base.relative_to(base.parents[2])
    for directory in (copy.parents[1], copy.parent, copy):
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
    source_lib, new = str(base / "lib"), str(root).encode()
    rewritten: dict[str, list[int]] = {}
    for walked, directories, files in os.walk(base):
        directories.sort()
        relative = Path(walked).relative_to(base)
        for name in sorted((*directories, *files)):
            source, made = Path(walked) / name, copy / relative / name
            info = source.lstat()
            if stat.S_ISLNK(info.st_mode):
                os.symlink(os.readlink(source), made)
            elif stat.S_ISDIR(info.st_mode):
                made.mkdir(mode=0o755)
                made.chmod(0o755)
            elif stat.S_ISREG(info.st_mode):
                data = bytearray(source.read_bytes())
                if data[:4] == b"\x7fELF":
                    offsets = relocate_search_path(data, source_lib, new)
                    if offsets:
                        rewritten[str(relative / name)] = offsets
                fd = os.open(
                    made, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                made.chmod(0o755 if info.st_mode & 0o111 else 0o644)
            else:
                raise ValueError("private interpreter has an unsupported entry")
    check_private_copy(copy)
    old = prefix.encode()
    objects = []
    for member, offsets in sorted(rewritten.items()):
        before, after = (base / member).read_bytes(), (copy / member).read_bytes()
        expected = bytearray(before)
        for at in offsets:
            expected[at : at + len(new)] = new
        if after != expected:  # same size, differing only in the rewritten bytes
            raise ValueError("private interpreter rewrite exceeds its search path")
        objects.append(
            {
                "path": member,
                "offsets": offsets,
                "before_sha256": hashlib.sha256(before).hexdigest(),
                "after_sha256": hashlib.sha256(after).hexdigest(),
            }
        )
    source_digest = tree_digest(base)
    normalized = tree_digest(copy, rewritten, old)
    if normalized != source_digest:
        raise ValueError("private interpreter digest mismatch")
    return (
        root,
        copy,
        {
            "source_tree_sha256": source_digest,
            "copy_tree_sha256": tree_digest(copy),
            "normalized_copy_tree_sha256": normalized,
            "rewritten_objects": objects,
        },
    )


def part_shards(part: int) -> list[int]:
    """The global shard indices one part runs."""
    if not 1 <= part <= ACCEPTANCE_PARTS:
        raise ValueError("unknown acceptance part")
    first = (part - 1) * SHARDS_PER_PART + 1
    return list(range(first, first + SHARDS_PER_PART))


def acceptance_plan(
    script: Path, weights: Path
) -> Callable[[dict[str, int]], list[list[str]]]:
    """Recompute the global shard plan with the acceptance runner's own placement."""
    spec = importlib.util.spec_from_file_location("planned_acceptance", script)
    if spec is None or spec.loader is None:
        raise ValueError("acceptance runner cannot be loaded")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    seconds = json.loads(weights.read_text())["seconds"]
    recorded = {str(module): float(value) for module, value in seconds.items()}

    def plan(discovered: dict[str, int]) -> list[list[str]]:
        return [
            list(runner.shard_modules(discovered, recorded, index, ACCEPTANCE_SHARDS))
            for index in range(1, ACCEPTANCE_SHARDS + 1)
        ]

    return plan


def check_shards(
    root: Path,
    count: int = ACCEPTANCE_SHARDS,
    indices: Sequence[int] | None = None,
    plan: Callable[[dict[str, int]], list[list[str]]] | None = None,
) -> None:
    """Listed shards (default: all) ran their planned modules completely.

    Only when every shard of the count is listed must the union be the suite.
    """
    listed = list(range(1, count + 1) if indices is None else indices)
    if (
        not listed
        or len(set(listed)) != len(listed)
        or not set(listed) <= set(range(1, count + 1))
    ):
        raise ValueError("invalid shard selection")
    discovered: dict[str, int] | None = None
    identities: dict[str, dict[str, int]] | None = None
    planned: list[list[str]] | None = None
    selected: set[str] = set()
    for index in listed:
        report = json.loads((root / f"acceptance-{index}.json").read_text())
        if discovered is None:
            discovered = report["discovered_modules"]
            identities = identity_inventory(report.get("discovered_tests"))
            if {m: sum(c.values()) for m, c in identities.items()} != discovered:
                raise ValueError("installed identity and module counts disagree")
            if plan is not None:
                planned = plan(discovered)
                if len(planned) != count:
                    raise ValueError("shard plan does not cover the shard count")
        if report["discovered_modules"] != discovered:
            raise ValueError("shards discovered different suites")
        if identity_inventory(report.get("discovered_tests")) != identities:
            raise ValueError("shards discovered different test identities")
        modules = set(report["selected_modules"])
        if (
            modules & selected
            or len(modules) != len(report["selected_modules"])
            or not modules <= discovered.keys()
            or report["skipped"]
            or report["errors"]
            or report["failures"]
        ):
            raise ValueError("overlap, skips or failed installed tests")
        if planned is not None and modules != set(planned[index - 1]):
            raise ValueError("installed shard ran other than its planned modules")
        if report["tests_run"] != sum(discovered[m] for m in modules):
            raise ValueError("incomplete installed shard")
        assert identities is not None
        if identity_inventory(report.get("executed_tests")) != {
            module: identities[module] for module in modules
        }:
            raise ValueError("installed test identities or multiplicities differ")
        selected |= modules
    if not discovered or (len(listed) == count and selected != set(discovered)):
        raise ValueError("missing installed test modules")


def retain_containment_failure(
    test_root: Path, evidence: Path, metadata: dict[str, Any]
) -> None:
    path = test_root / "query-host-containment-fatal.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return
    metadata["proof_complete"] = False
    metadata["containment_fatal"] = True
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise RuntimeError("unsafe containment evidence")
        data = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    if len(data) > 65536:
        raise RuntimeError("containment evidence limit")
    record = json.loads(data)
    if record.get("fatal") is not True or record.get("cleanup") != "UNKNOWN":
        raise RuntimeError("invalid containment failure record")
    output = os.open(
        evidence / path.name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(output, "wb") as stream:
        stream.write(data)
    metadata["containment_record_sha256"] = hashlib.sha256(data).hexdigest()


class ResourceSampler:
    """Runner CPU, memory and pressure while the proof runs, for its evidence.

    Observation only: a read failure ends sampling, never the proof. Each sample
    is one JSON line; `stop()` summarises CPU busy and iowait shares, load,
    lowest available memory and the highest pressure per marked phase.
    """

    def __init__(
        self,
        path: Path,
        proc: Path = Path("/proc"),
        interval: float = RESOURCE_SAMPLE_SECONDS,
    ) -> None:
        self.path, self.proc, self.interval = path, proc, interval
        self.samples: list[dict[str, Any]] = []
        self.phase = "setup"
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.started = time.monotonic()

    def start(self) -> None:
        self.thread.start()

    def mark(self, phase: str) -> None:
        self.phase = phase

    def sample(self) -> dict[str, Any]:
        cpu = [
            int(v) for v in (self.proc / "stat").read_text().split("\n")[0].split()[1:]
        ]
        memory = {
            key: int(value.split()[0])
            for key, value in (
                line.split(":", 1)
                for line in (self.proc / "meminfo").read_text().splitlines()
                if ":" in line
            )
            if key in ("MemTotal", "MemAvailable")
        }
        pressure: dict[str, float] = {}
        for kind in ("cpu", "io", "memory"):
            try:
                first = (self.proc / "pressure" / kind).read_text().splitlines()[0]
            except (OSError, IndexError):
                continue
            fields = dict(part.split("=", 1) for part in first.split()[1:])
            pressure[kind] = float(fields["avg10"])
        return {
            "t": round(time.monotonic() - self.started, 1),
            "phase": self.phase,
            "load1": float((self.proc / "loadavg").read_text().split()[0]),
            "cpu": cpu,
            "mem_kib": memory,
            "pressure_some_avg10": pressure,
        }

    def _loop(self) -> None:
        try:
            with self.path.open("a") as stream:
                while True:
                    record = self.sample()
                    self.samples.append(record)
                    stream.write(json.dumps(record, sort_keys=True) + "\n")
                    stream.flush()
                    if self.stopped.wait(self.interval):
                        return
        except (OSError, ValueError, KeyError, IndexError):
            return

    def stop(self) -> dict[str, Any]:
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join(self.interval + 5)
        phases: dict[str, dict[str, Any]] = {}
        for before, after in zip(self.samples, self.samples[1:]):
            delta = [b - a for a, b in zip(before["cpu"], after["cpu"])]
            total = sum(delta) or 1
            idle, iowait = delta[3], delta[4] if len(delta) > 4 else 0
            row = phases.setdefault(
                after["phase"], {"intervals": 0, "busy": 0.0, "iowait": 0.0}
            )
            row["intervals"] += 1
            row["busy"] += (total - idle - iowait) / total
            row["iowait"] += iowait / total
            row["load1_max"] = max(row.get("load1_max", 0.0), after["load1"])
            available = after["mem_kib"].get("MemAvailable")
            if available is not None:
                row["mem_available_min_kib"] = min(
                    row.get("mem_available_min_kib", available), available
                )
            for kind, value in after["pressure_some_avg10"].items():
                key = f"{kind}_pressure_avg10_max"
                row[key] = max(row.get(key, 0.0), value)
        for row in phases.values():
            row["busy_mean"] = round(row.pop("busy") / row["intervals"], 3)
            row["iowait_mean"] = round(row.pop("iowait") / row["intervals"], 3)
        first = self.samples[0] if self.samples else {}
        return {
            "cpu_count": os.cpu_count(),
            "mem_total_kib": first.get("mem_kib", {}).get("MemTotal"),
            "interval_seconds": self.interval,
            "samples": len(self.samples),
            "phases": phases,
        }


def qualify(
    checkout: Path, artifacts: Path, evidence: Path, root: Path, sha: str, part: int
) -> None:
    shards = part_shards(part)
    runner = Commands(evidence)
    sampler = ResourceSampler(evidence / "runner-resources.jsonl")
    sampler.start()
    test_root: Path | None = None
    python = str(Path(sys.executable).resolve())
    metadata: dict[str, Any] = {
        "source_sha": sha,
        "postgres_index": POSTGRES,
        "python": sys.version,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "runner_os": platform.platform(),
        "architecture": platform.machine(),
        "part": part,
        "parts": ACCEPTANCE_PARTS,
        "shards": shards,
        "global_shards": ACCEPTANCE_SHARDS,
        "host_proof_part": HOST_PROOF_PART,
        # A part record never completes a proof; only the aggregation does.
        "part_complete": False,
        "qualification_requires": [
            "successful part job",
            "verified PostgreSQL stop",
            "aggregation of every part of the interpreter",
        ],
        "child_file_limit_bytes": MAX_LOG_BYTES,
    }
    try:
        runner.run(
            "verify-source", ["git", "diff", "--exit-code", "HEAD", "--"], checkout
        )
        actual = runner.capture(
            "source-head", ["git", "rev-parse", "HEAD"], checkout
        ).strip()
        if actual != sha or not re.fullmatch("[0-9a-f]{40}", sha):
            raise ValueError("checkout source identity mismatch")
        wheels = list(artifacts.glob("*.whl"))
        sdists = list(artifacts.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise ValueError("expected one reviewed wheel and sdist")
        wheel, sdist = wheels[0], sdists[0]
        metadata["artifacts"] = {
            p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
            for p in (wheel, sdist)
        }
        runner.run(
            "verify-artifacts",
            [
                python,
                "scripts/inspect_python_distribution.py",
                str(artifacts),
                "--source-commit",
                sha,
                "--check-inventory",
                str(artifacts / "python-package-artifacts.json"),
            ],
            checkout,
        )
        dependencies = root / "dependencies"
        runner.run(
            "dependencies",
            [
                python,
                "-m",
                "pip",
                "download",
                *RUNTIME_DEPS,
                "--only-binary=:all:",
                "--dest",
                str(dependencies),
            ],
            checkout,
        )
        (evidence / "dependency-hashes.json").write_text(
            json.dumps(
                {p.name: file_hash(p) for p in sorted(dependencies.iterdir())}, indent=2
            )
            + "\n"
        )
        runner.run(
            "build-tools",
            [python, "-m", "pip", "install", "setuptools==80.9.0", "wheel==0.45.1"],
            checkout,
        )
        rebuilt = root / "from-sdist"
        runner.run(
            "sdist-wheel",
            [
                python,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                str(sdist),
                "--wheel-dir",
                str(rebuilt),
            ],
            checkout,
        )
        rebuilt_wheels = list(rebuilt.glob("*.whl"))
        if len(rebuilt_wheels) != 1 or file_hash(wheel) != file_hash(rebuilt_wheels[0]):
            raise ValueError("reviewed and sdist-rebuilt wheel bytes differ")
        metadata["sdist_wheel_equal"] = True
        # The runner image leaves the toolcache world-writable (chmod -R 777 /opt)
        # and its ELF objects search <toolcache>/lib. Everything from the venv on
        # runs on a private S-owned copy whose search path is relocated into it.
        base = Path(sys.base_prefix).resolve()
        if not Path(python).is_relative_to(base):
            raise ValueError("interpreter outside its base prefix")
        private, copy, relocation = private_interpreter(base)
        copy_python = copy / Path(python).relative_to(base)
        metadata["interpreter"] = {
            "build": "setup-python",
            "version": base.parts[-2],
            "architecture": base.parts[-1],
            "relocation": "verified same-length RUNPATH prefix rewrite",
            "source_python_sha256": file_hash(Path(python)),
            "python_sha256": file_hash(copy_python),
            "setup_python_steps": [
                "verify-artifacts",
                "dependencies",
                "build-tools",
                "sdist-wheel",
            ],
            "private_copy_steps": "venv and every later step",
            **relocation,
        }
        for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"):
            os.environ.pop(name, None)
        test_root = private / "installed"
        test_root.mkdir(mode=0o755)
        runner.run(
            "venv", [str(copy_python), "-m", "venv", str(test_root / "venv")], checkout
        )
        installed_python = str(test_root / "venv/bin/python")
        runner.run(
            "install-wheel",
            [
                installed_python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(dependencies),
                str(wheel),
            ],
            checkout,
        )
        inputs = [checkout / "scripts" / name for name in SCRIPTS]
        inputs += [
            checkout / "contracts" / name
            for name in ("runtime-inventory.json", "public-registry.json")
        ]
        inputs += sorted((checkout / "tests/migrations").glob("*.py"))
        inputs += sorted((checkout / "tests/runtime").glob("*.py"))
        inputs += [checkout / "tests/unit/test_contract_catalog_preview.py"]
        inputs += sorted((checkout / "tests/fixtures").glob("*.json"))
        # Copy only committed public files; never a local evidence directory.
        tracked = set(
            runner.capture("tracked-inputs", ["git", "ls-files"], checkout).splitlines()
        )
        names: set[str] = set()
        for source in inputs:
            if (
                source.is_symlink()
                or str(source.relative_to(checkout)) not in tracked
                or source.name in names
            ):
                raise ValueError("unexpected copied test input: " + source.name)
            names.add(source.name)
            shutil.copyfile(source, test_root / source.name)
        metadata["copied_inputs"] = {
            str(p.relative_to(checkout)): file_hash(p) for p in inputs
        }
        environment_probe = (
            "import importlib.metadata as m,json,os,psycopg,sys; "
            "c=psycopg.connect(os.environ['N1_TEST_DATABASE_URL']); "
            "v=c.execute('SHOW server_version').fetchone()[0]; c.close(); "
            "assert v.startswith('18.4'), v; "
            "print(json.dumps({'python':sys.version,'postgres':v,"
            "'distributions':{d.metadata['Name']:d.version for d in m.distributions()}}))"
        )
        metadata["installed_environment"] = json.loads(
            runner.capture(
                "installed-environment",
                [installed_python, "-I", "-c", environment_probe],
                test_root,
            )
        )
        # The run-as host worker refuses client-writable inputs; learn that now,
        # not after the complete acceptance and restart proofs.
        roots = json.loads(
            runner.capture(
                "installed-import-roots",
                [
                    installed_python,
                    "-I",
                    "-c",
                    "import json,sys;print(json.dumps(sys.path))",
                ],
                test_root,
            )
        )
        mapped = json.loads(
            runner.capture(
                "installed-mapped-objects",
                [installed_python, "-I", "-c", MAPS_PROBE],
                test_root,
            )
        )
        check_client_unwritable(
            installed_python,
            test_root / "query_host_cli_identity.py",
            roots,
            pwd.getpwnam("nobody").pw_uid,
            os.getegid(),
            tuple(mapped),
            (copy, test_root / "venv"),
        )
        metadata["client_unwritable_inputs"] = True
        runner.run(
            "package-isolation",
            [installed_python, "-I", "prove_package_isolation.py"],
            test_root,
        )
        runner.run(
            "release-installation",
            [installed_python, "-I", "prove_release_installation.py"],
            test_root,
        )
        sampler.mark("acceptance")
        children = [
            runner.start(
                f"acceptance-{i}",
                [
                    installed_python,
                    "-I",
                    "run_installed_acceptance.py",
                    "--shard",
                    f"{i}/{ACCEPTANCE_SHARDS}",
                    "--result-json",
                    f"acceptance-{i}.json",
                ],
                test_root,
            )
            for i in shards
        ]
        exits = [
            runner.finish(child) for child in children
        ]  # collect every exit, including peers of failures
        for i in shards:
            report = test_root / f"acceptance-{i}.json"
            if report.is_file():
                shutil.copyfile(report, evidence / report.name)
        if any(exits):
            raise RuntimeError("installed acceptance failed; every shard exit retained")
        check_shards(
            test_root,
            ACCEPTANCE_SHARDS,
            shards,
            acceptance_plan(
                test_root / "run_installed_acceptance.py",
                test_root / "acceptance-shard-weights.json",
            ),
        )
        if part != HOST_PROOF_PART:
            metadata["part_complete"] = True
            return
        sampler.mark("restarts")
        restart_failures = []
        for index, script in enumerate(RESTARTS, 1):
            try:
                runner.run(
                    f"restart-{index}-prepare",
                    [installed_python, "-I", script, "prepare"],
                    test_root,
                )
                runner.run(
                    f"restart-{index}-recover",
                    [installed_python, "-I", script, "recover"],
                    test_root,
                )
            except Exception as error:
                restart_failures.append(str(error))
            finally:
                # Cleanup shares one reserved global deadline, not a new budget per child.
                try:
                    runner.run(
                        f"restart-{index}-cleanup",
                        [installed_python, "-I", script, "cleanup"],
                        test_root,
                        cleanup=True,
                    )
                except Exception as error:
                    restart_failures.append(str(error))
        if restart_failures:
            raise RuntimeError("; ".join(restart_failures))
        sampler.mark("host-proof")
        host_environment = prepare_host_binding(
            runner, test_root, installed_python, sha, wheel
        )
        metadata["host_binding_sha256"] = host_environment[HOST_ENV[1]]
        runner.run(
            "query-host",
            [installed_python, "-I", "prove_query_host.py"],
            test_root,
            env=host_environment,
        )
        metadata["part_complete"] = True
    except BaseException as error:
        metadata["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            metadata["runner_resources"] = sampler.stop()
        except Exception as error:  # observation never decides the proof
            metadata["runner_resources"] = {"error": type(error).__name__}
        try:
            runner.close()
        except BaseException as error:
            metadata["part_complete"] = False
            metadata["cleanup_failure"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            evidence_error: Exception | None = None
            try:
                if test_root is not None:
                    retain_containment_failure(test_root, evidence, metadata)
            except Exception as error:
                metadata["part_complete"] = False
                metadata["containment_evidence_failure"] = type(error).__name__
                evidence_error = error
            (evidence / PART_RECORD).write_text(json.dumps(metadata, indent=2) + "\n")
            if evidence_error is not None:
                raise evidence_error
            if metadata.get("containment_fatal"):
                raise RuntimeError(
                    "query host containment failed; later proof forbidden"
                )


def part_artifact_name(
    minor: str, part: int, sha: str, run_id: str, attempt: str
) -> str:
    return f"installed-qualification-part-{minor}-{part}-{sha}-{run_id}-{attempt}"


def gh(arguments: list[str]) -> bytes:
    """GitHub's CLI with the job's read token; its output is data, never run."""
    return subprocess.run(
        ["gh", *arguments], check=True, capture_output=True, timeout=120
    ).stdout


def checkout_identity(checkout: Path) -> str:
    """The clean checkout's HEAD; the aggregation reads plan inputs from it."""
    subprocess.run(
        ["git", "diff", "--quiet", "--exit-code", "HEAD", "--"],
        cwd=checkout,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def extract_artifact(data: bytes, target: Path) -> None:
    """Unpack an artifact zip into a fresh directory, refusing links and escapes."""
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            name = member.filename
            parts = Path(name).parts
            if (
                not parts
                or name.startswith("/")
                or "\\" in name
                or ".." in parts
                or stat.S_ISLNK(member.external_attr >> 16)
            ):
                raise ValueError("unsafe artifact member: " + name)
            destination = target / name
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("xb") as sink:
                shutil.copyfileobj(source, sink)


def fetch_parts(
    fetch: Callable[[list[str]], bytes],
    root: Path,
    minor: str,
    sha: str,
    run_id: str,
    attempt: str,
) -> dict[int, dict[str, Any]]:
    """Every part's artifact of this run attempt, verified against GitHub's digest."""
    listing = json.loads(
        fetch(
            [
                "api",
                "--method",
                "GET",
                f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts",
                "-F",
                "per_page=100",
            ]
        )
    )
    artifacts = listing.get("artifacts")
    if not isinstance(artifacts, list) or listing.get("total_count") != len(artifacts):
        raise ValueError("incomplete run artifact listing")
    parts: dict[int, dict[str, Any]] = {}
    for part in range(1, ACCEPTANCE_PARTS + 1):
        name = part_artifact_name(minor, part, sha, run_id, attempt)
        rows = [row for row in artifacts if row.get("name") == name]
        if len(rows) != 1:
            raise ValueError("missing or duplicate part artifact: " + name)
        row = rows[0]
        digest = str(row.get("digest") or "")
        run = row.get("workflow_run") or {}
        if (
            row.get("expired") is not False
            or not re.fullmatch("sha256:[0-9a-f]{64}", digest)
            or str(run.get("id")) != run_id
            or run.get("head_sha") != sha
        ):
            raise ValueError("part artifact is not this run's intact upload: " + name)
        data = fetch(
            ["api", f"repos/{REPOSITORY}/actions/artifacts/{int(row['id'])}/zip"]
        )
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("part artifact differs from its recorded digest: " + name)
        directory = root / f"part-{part}"
        extract_artifact(data, directory)
        parts[part] = {
            "name": name,
            "id": int(row["id"]),
            "digest": digest,
            "zip_bytes": len(data),
            "directory": directory,
        }
    return parts


def check_part(
    evidence: Path,
    part: int,
    expected: dict[str, Any],
    artifacts: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One part's record and command manifest; returns it and its shared identity."""
    record = json.loads((evidence / PART_RECORD).read_text())
    for key, value in {
        **expected,
        "part": part,
        "shards": part_shards(part),
        "part_complete": True,
        "sdist_wheel_equal": True,
        "client_unwritable_inputs": True,
        "artifacts": artifacts,
    }.items():
        if record.get(key) != value:
            raise ValueError(f"part {part} record differs in {key}")
    if part == HOST_PROOF_PART and not record.get("host_binding_sha256"):
        raise ValueError("host proof part has no host binding")
    if (evidence / "postgres-cleanup-exit.txt").read_text().strip() != "0":
        raise ValueError(f"part {part} PostgreSQL stop is unverified")
    rows = json.loads((evidence / "commands.json").read_text())
    names = [
        *SETUP_COMMANDS,
        *(f"acceptance-{index}" for index in part_shards(part)),
        *(HOST_COMMANDS if part == HOST_PROOF_PART else ()),
    ]
    if [row.get("name") for row in rows] != names:
        raise ValueError(f"part {part} ran other than its planned commands")
    reports = {
        report.name
        for report in evidence.iterdir()
        if re.fullmatch(r"acceptance-\d+\.json", report.name)
    }
    if reports != {f"acceptance-{index}.json" for index in part_shards(part)}:
        raise ValueError(f"part {part} holds other than its own shard reports")
    if part == HOST_PROOF_PART:
        shards_ended = max(
            float(row["finished"])
            for row in rows
            if row["name"].startswith("acceptance-")
        )
        if min(
            float(row["started"]) for row in rows if row["name"] in HOST_COMMANDS
        ) < (shards_ended):
            raise ValueError("restarts or host proof started before acceptance ended")
    for row in rows:
        log = evidence / str(row.get("log"))
        if (
            row.get("log") != f"{row['name']}.log"
            or not row.get("complete")
            or row.get("exit_code") != 0
            or row.get("deadline_exhausted")
            or file_hash(log) != row.get("log_sha256")
        ):
            raise ValueError(f"part {part} command {row['name']} is unproven")
    record["driver_span_seconds"] = round(
        max(float(row["finished"]) for row in rows)
        - min(float(row["started"]) for row in rows),
        1,
    )
    identity = {
        "interpreter": {k: record["interpreter"][k] for k in INTERPRETER_IDENTITY},
        "dependency_hashes": json.loads(
            (evidence / "dependency-hashes.json").read_text()
        ),
        "installed_environment": record["installed_environment"],
        "postgres_image_id": (evidence / "postgres-image-id.txt").read_text().strip(),
        "copied_inputs": record["copied_inputs"],
    }
    return record, identity


def aggregate(
    checkout: Path,
    package: Path,
    evidence: Path,
    root: Path,
    sha: str,
    minor: str,
    parts_result: str,
    run_id: str,
    attempt: str,
    fetch: Callable[[list[str]], bytes] = gh,
) -> None:
    """Complete an interpreter's proof from every part, or record why not."""
    metadata: dict[str, Any] = {
        "source_sha": sha,
        "python_minor": minor,
        "run_id": run_id,
        "run_attempt": attempt,
        "postgres_index": POSTGRES,
        "parts": ACCEPTANCE_PARTS,
        "shards_per_part": SHARDS_PER_PART,
        "global_shards": ACCEPTANCE_SHARDS,
        "host_proof_part": HOST_PROOF_PART,
        "shard_index_map": {
            str(part): part_shards(part) for part in range(1, ACCEPTANCE_PARTS + 1)
        },
        # needs.compatibility.result covers every part of both interpreters, so
        # a failed part of either interpreter also refuses this aggregation.
        "parts_result_scope": "every part job of both interpreters",
        "parts_result": parts_result,
        "proof_complete": False,
    }
    manifest: dict[str, Any] = {"source_sha": sha, "run_id": run_id, "parts": {}}
    try:
        if parts_result != "success":
            raise ValueError("every part job must succeed, not: " + parts_result)
        # The plan is recomputed from this checkout, so it must be the exact source.
        head = checkout_identity(checkout)
        metadata["checkout_head"] = head
        if head != sha:
            raise ValueError("aggregation checkout differs from the exact source")
        wheels, sdists = list(package.glob("*.whl")), list(package.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise ValueError("expected one reviewed wheel and sdist")
        artifacts = {
            p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
            for p in (wheels[0], sdists[0])
        }
        metadata["artifacts"] = artifacts
        expected = {
            "source_sha": sha,
            "run_id": run_id,
            "run_attempt": attempt,
            "postgres_index": POSTGRES,
            "parts": ACCEPTANCE_PARTS,
            "global_shards": ACCEPTANCE_SHARDS,
            "host_proof_part": HOST_PROOF_PART,
        }
        shards = root / "shards"
        shards.mkdir()
        identities = []
        for part, artifact in fetch_parts(
            fetch, root, minor, sha, run_id, attempt
        ).items():
            part_evidence = artifact["directory"] / "qualification"
            record, identity = check_part(part_evidence, part, expected, artifacts)
            if not str(identity["interpreter"]["version"]).startswith(minor + "."):
                raise ValueError(f"part {part} ran another interpreter")
            identities.append(identity)
            for index in part_shards(part):
                shutil.copyfile(
                    part_evidence / f"acceptance-{index}.json",
                    shards / f"acceptance-{index}.json",
                )
            manifest["parts"][str(part)] = {
                **{k: v for k, v in artifact.items() if k != "directory"},
                "record_sha256": file_hash(part_evidence / PART_RECORD),
                "driver_span_seconds": record["driver_span_seconds"],
                "runner_resources": record.get("runner_resources"),
            }
        if sorted(report.name for report in shards.iterdir()) != sorted(
            f"acceptance-{index}.json" for index in range(1, ACCEPTANCE_SHARDS + 1)
        ):
            raise ValueError("the parts do not hold every global shard exactly once")
        if any(identity != identities[0] for identity in identities):
            raise ValueError(
                "parts differ in interpreter, dependencies, environment, image or inputs"
            )
        copied = identities[0]["copied_inputs"]
        if not set(PLAN_INPUTS) <= set(copied) or copied != {
            path: file_hash(checkout / path) for path in copied
        }:
            raise ValueError("copied inputs differ from the exact source")
        check_shards(
            shards,
            ACCEPTANCE_SHARDS,
            None,
            acceptance_plan(checkout / PLAN_INPUTS[0], checkout / PLAN_INPUTS[1]),
        )
        report = json.loads((shards / "acceptance-1.json").read_text())
        metadata["census"] = {
            "occurrences": sum(report["discovered_modules"].values()),
            "ids": sum(len(tests) for tests in report["discovered_tests"].values()),
            "modules": len(report["discovered_modules"]),
        }
        metadata.update(
            {k: v for k, v in identities[0].items() if k != "copied_inputs"}
        )
        metadata["copied_inputs"] = copied
        metadata["proof_complete"] = True
    except BaseException as error:
        metadata["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (evidence / "aggregation-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )
        (evidence / "qualification.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modes = parser.add_subparsers(dest="mode", required=True)
    one = modes.add_parser("part", help="run one acceptance part of the proof")
    one.add_argument(
        "--part", type=int, choices=range(1, ACCEPTANCE_PARTS + 1), required=True
    )
    one.add_argument("--artifacts", type=Path, required=True)
    combined = modes.add_parser("aggregate", help="complete one interpreter's proof")
    combined.add_argument("--python-minor", required=True)
    combined.add_argument("--package", type=Path, required=True)
    combined.add_argument("--parts-result", required=True)
    for mode in (one, combined):
        mode.add_argument("--evidence", type=Path, required=True)
        mode.add_argument("--root", type=Path, required=True)
        mode.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
    ):
        raise ValueError("this driver is restricted to isolated Linux Actions jobs")
    if args.mode == "aggregate":
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if (
            os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or not run_id.isdigit()
            or not attempt.isdigit()
            or args.python_minor not in ("3.13", "3.14")
        ):
            raise ValueError("aggregation needs this repository's exact run attempt")
        args.root.mkdir()
        args.evidence.mkdir(parents=True, exist_ok=True)
        aggregate(
            Path.cwd().resolve(),
            args.package.resolve(),
            args.evidence.resolve(),
            args.root.resolve(),
            args.source_sha,
            args.python_minor,
            args.parts_result,
            run_id,
            attempt,
        )
        return
    if os.environ.get("N1_TEST_DATABASE_URL") != (
        "postgresql://postgres:synthetic@127.0.0.1:5432/postgres"
    ):
        raise ValueError("only the isolated fictional loopback service is eligible")
    os.environ.pop("PYTHONPATH", None)

    # Interruptions still close owned child groups and retain failure metadata.
    def interrupted(signum: int, frame: Any) -> None:
        raise RuntimeError(f"qualification interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    # The run-as client shares this job's group: create nothing it could write,
    # whatever umask the job inherited. Children inherit this umask.
    os.umask(0o022)
    # Created by this job, not a reusable local qualification environment.
    args.root.mkdir()
    args.evidence.mkdir(parents=True, exist_ok=True)
    qualify(
        Path.cwd().resolve(),
        args.artifacts.resolve(),
        args.evidence.resolve(),
        args.root.resolve(),
        args.source_sha,
        args.part,
    )


if __name__ == "__main__":
    main()
