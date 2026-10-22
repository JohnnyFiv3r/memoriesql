"""Run the existing installed fictional proof, retaining every child outcome."""

from __future__ import annotations

import argparse
import hashlib
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
import time
import zipfile
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


def check_shards(root: Path, count: int = 3) -> None:
    discovered: dict[str, int] | None = None
    identities: dict[str, dict[str, int]] | None = None
    selected: set[str] = set()
    for index in range(1, count + 1):
        report = json.loads((root / f"acceptance-{index}.json").read_text())
        if discovered is None:
            discovered = report["discovered_modules"]
            identities = identity_inventory(report.get("discovered_tests"))
            if {m: sum(c.values()) for m, c in identities.items()} != discovered:
                raise ValueError("installed identity and module counts disagree")
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
        if report["tests_run"] != sum(discovered[m] for m in modules):
            raise ValueError("incomplete installed shard")
        assert identities is not None
        if identity_inventory(report.get("executed_tests")) != {
            module: identities[module] for module in modules
        }:
            raise ValueError("installed test identities or multiplicities differ")
        selected |= modules
    if not discovered or selected != set(discovered):
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


def qualify(
    checkout: Path, artifacts: Path, evidence: Path, root: Path, sha: str
) -> None:
    runner = Commands(evidence)
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
        "proof_complete": False,
        "qualification_requires": [
            "successful interpreter job",
            "verified PostgreSQL stop",
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
        children = [
            runner.start(
                f"acceptance-{i}",
                [
                    installed_python,
                    "-I",
                    "run_installed_acceptance.py",
                    "--shard",
                    f"{i}/3",
                    "--result-json",
                    f"acceptance-{i}.json",
                ],
                test_root,
            )
            for i in range(1, 4)
        ]
        exits = [
            runner.finish(child) for child in children
        ]  # collect every exit, including peers of failures
        for report in test_root.glob("acceptance-*.json"):
            shutil.copyfile(report, evidence / report.name)
        if any(exits):
            raise RuntimeError("installed acceptance failed; every shard exit retained")
        check_shards(test_root)
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
        metadata["proof_complete"] = True
    except BaseException as error:
        metadata["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            runner.close()
        except BaseException as error:
            metadata["proof_complete"] = False
            metadata["cleanup_failure"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            evidence_error: Exception | None = None
            try:
                if test_root is not None:
                    retain_containment_failure(test_root, evidence, metadata)
            except Exception as error:
                metadata["proof_complete"] = False
                metadata["containment_evidence_failure"] = type(error).__name__
                evidence_error = error
            (evidence / "qualification.json").write_text(
                json.dumps(metadata, indent=2) + "\n"
            )
            if evidence_error is not None:
                raise evidence_error
            if metadata.get("containment_fatal"):
                raise RuntimeError(
                    "query host containment failed; later proof forbidden"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
    ):
        raise ValueError("this driver is restricted to isolated Linux Actions jobs")
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
    )


if __name__ == "__main__":
    main()
