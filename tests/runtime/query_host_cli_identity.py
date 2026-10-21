"""Fixed, test-only C:G client worker; privileged execution remains unapproved.

The operator must supply the separately approved, S-owned binding. No missing
binding, unknown birth or failed monitor can become a successful proof. Unknown
lineage is fatal to the job; VM disposal is failure infrastructure, not cleanup.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn

MAX_FRAME = 65536
MAX_OUTPUT = 262144
FATAL_NAME = "query-host-containment-fatal.json"
NORMAL_ENV = "N1_QUALIFICATION_DEADLINE"
CLEANUP_ENV = "N1_QUALIFICATION_CLEANUP_DEADLINE"


class ContainmentFatal(RuntimeError):
    """Stop later proof; the runner, not a normal assertion, latches this."""

    fatal_containment = True


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if not math.isfinite(value) or value <= 0:
        raise ContainmentFatal("shared_deadline_exhausted")
    return value


def check_identity(value: dict[str, Any], plan: dict[str, Any]) -> None:
    if value.get("uids") != [plan["client_uid"]] * 4:
        raise ContainmentFatal("unexpected_real_effective_saved_uid")
    if value.get("gids") != [plan["gid"]] * 4:
        raise ContainmentFatal("unexpected_real_effective_saved_gid")
    groups = value.get("groups")
    if not isinstance(groups, list) or not set(groups) <= set(plan["groups"]):
        raise ContainmentFatal("unexpected_supplementary_groups")
    if value.get("caps") != [0, 0, 0]:
        raise ContainmentFatal("retained_capabilities")


def process(pid: int) -> dict[str, Any] | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        status = Path(f"/proc/{pid}/status").read_text()
    except FileNotFoundError:
        return None
    fields = text[text.rindex(")") + 2 :].split()
    values = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    return {
        "pid": pid,
        "start": int(fields[19]),
        "parent": int(fields[1]),
        "uids": list(map(int, values["Uid"].split())),
        "gids": list(map(int, values["Gid"].split())),
        "groups": list(map(int, values["Groups"].split())),
        "caps": [
            int(values[name].strip(), 16) for name in ("CapEff", "CapPrm", "CapAmb")
        ],
    }


def same_process(expected: dict[str, Any], current: dict[str, Any] | None) -> bool:
    return current is not None and all(
        expected.get(key) == current.get(key)
        for key in ("pid", "start", "uids", "gids")
    )


def check_entry(info: os.stat_result, uid: int, private: bool = False) -> None:
    if info.st_uid != uid or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ContainmentFatal("substituted_manifest_or_evidence")
    if info.st_mode & (0o077 if private else 0o022):
        raise ContainmentFatal("writable_manifest_or_evidence")


def read_binding(path: Path, digest: str) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        data = os.read(fd, MAX_FRAME + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_FRAME or hashlib.sha256(data).hexdigest() != digest:
        raise ContainmentFatal("manifest_binding_mismatch")
    plan = json.loads(data)
    check_entry(info, plan["service_uid"])
    for name in ("service_uid", "client_uid", "gid"):
        if type(plan[name]) is not int or plan[name] < 0:
            raise ContainmentFatal("invalid_identity_binding")
    if (
        plan["service_uid"] == 0
        or plan["gid"] == 0
        or plan["client_uid"] in (0, plan["service_uid"])
    ):
        raise ContainmentFatal("identities_not_distinct_nonroot")
    return plan


def read_plan(path: Path, digest: str) -> dict[str, Any]:
    plan = read_binding(path, digest)
    if plan["python"] != str(Path(plan["python"]).absolute()) or plan["helper"] != str(
        Path(__file__).resolve()
    ):
        raise ContainmentFatal("substituted_executable_or_helper")
    if plan["cleanup_deadline"] - plan["deadline"] != 120:
        raise ContainmentFatal("changed_shared_cleanup_budget")
    return plan


def latch(code: str, identities: list[dict[str, Any]]) -> None:
    """Only S writes this fixed, bounded, C-nonwritable uploader input."""
    data = json.dumps(
        {"fatal": True, "cleanup": "UNKNOWN", "code": code, "identities": identities},
        sort_keys=True,
    ).encode()
    if len(data) > MAX_FRAME:
        raise ContainmentFatal("failure_record_limit")
    try:
        fd = os.open(
            FATAL_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        check_entry(os.lstat(FATAL_NAME), os.geteuid(), private=True)
        return
    with os.fdopen(fd, "wb") as output:
        output.write(data)


class Ledger:
    def __init__(self) -> None:
        self.identities: list[dict[str, Any]] = []
        self.birth_pending = False
        self.fatal = False
        self.monitors: list[dict[str, Any]] = []

    def unknown(self, code: str) -> NoReturn:
        self.fatal = True
        latch(code, self.monitors + self.identities)
        raise ContainmentFatal(code)

    def birth(self) -> None:
        if self.fatal or self.birth_pending:
            self.unknown("overlapping_or_late_birth")
        self.birth_pending = True

    def register(
        self, claimed: dict[str, Any], plan: dict[str, Any], parent: int | None = None
    ) -> None:
        if self.fatal or not self.birth_pending:
            self.unknown("unrequested_or_post_fatal_registration")
        if any(
            type(claimed.get(key)) is not int or claimed[key] <= 0
            for key in ("pid", "start")
        ):
            self.unknown("invalid_process_identity")
        current = process(claimed["pid"])
        if not same_process(claimed, current):
            self.unknown("identity_substitution_or_worker_death")
        assert current is not None
        try:
            check_identity(current, plan)
        except ContainmentFatal:
            self.unknown("unexpected_registered_identity")
        if parent is not None and current["parent"] != parent:
            self.unknown("unproven_child_lineage")
        self.identities.append(current)
        self.birth_pending = False

    def complete(self) -> bool:
        return not self.fatal and not self.birth_pending


class Channel:
    def __init__(self, stream: Any, deadline: float) -> None:
        self.stream = stream
        self.deadline = deadline
        self.buffer = bytearray()
        self.total = 0

    def read(self) -> dict[str, Any]:
        while b"\n" not in self.buffer:
            if not select.select([self.stream], [], [], remaining(self.deadline))[0]:
                raise ContainmentFatal("protocol_deadline")
            data = os.read(self.stream.fileno(), 4096)
            if not data:
                raise ContainmentFatal("worker_eof_or_partial_registration")
            self.total += len(data)
            self.buffer.extend(data)
            if self.total > MAX_OUTPUT or len(self.buffer) > MAX_FRAME:
                raise ContainmentFatal("protocol_output_limit")
        line, _, tail = self.buffer.partition(b"\n")
        self.buffer = bytearray(tail)
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ContainmentFatal("invalid_protocol_frame")
        return value


def send(stream: Any, value: dict[str, Any], deadline: float) -> None:
    data = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise ContainmentFatal("protocol_frame_limit")
    os.set_blocking(stream.fileno(), False)
    while data:
        if not select.select([], [stream], [], remaining(deadline))[1]:
            raise ContainmentFatal("protocol_write_deadline")
        written = os.write(stream.fileno(), data[:4096])
        if written <= 0:
            raise ContainmentFatal("protocol_broken_pipe")
        data = data[written:]


def sudo_argv(plan: dict[str, Any], action: str, path: Path, digest: str) -> list[str]:
    if action not in ("run-case", "cleanup"):
        raise ContainmentFatal("unapproved_privileged_action")
    return [
        "/usr/bin/sudo",
        "-n",
        "-u",
        f"#{plan['client_uid']}",
        "-g",
        f"#{plan['gid']}",
        "--",
        "/usr/bin/env",
        "-i",
        plan["python"],
        "-I",
        plan["helper"],
        action,
        str(path),
        digest,
    ]


def cli_reply(stream: Any, deadline: float) -> dict[str, Any]:
    """Read the actual CLI's pretty JSON through bounded EOF, never one RPC line."""
    data = bytearray()
    os.set_blocking(stream.fileno(), False)
    while True:
        if not select.select([stream], [], [], remaining(deadline))[0]:
            raise ContainmentFatal("cli_output_or_eof_deadline")
        part = os.read(stream.fileno(), 4096)
        if not part:
            break
        data.extend(part)
        if len(data) > MAX_OUTPUT:
            raise ContainmentFatal("cli_output_limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ContainmentFatal("invalid_cli_reply")
    return value


def private_state(plan: dict[str, Any]) -> Path:
    area = Path(plan["root"]) / "client-area"
    name = "state-" + plan["nonce"]
    if len(plan["nonce"]) != 32 or any(
        c not in "0123456789abcdef" for c in plan["nonce"]
    ):
        raise ContainmentFatal("invalid_state_nonce")
    if (
        area.is_symlink()
        or area.resolve() != Path(plan["root"]).resolve() / "client-area"
    ):
        raise ContainmentFatal("state_parent_substitution")
    return area / name


def check_worker(plan: dict[str, Any], cleanup_phase: bool = False) -> None:
    # Execute only after the separately approved transition, never during import.
    os.closerange(3, int(os.sysconf("SC_OPEN_MAX")))
    identity = process(os.getpid())
    if identity is None:
        raise ContainmentFatal("worker_identity_unavailable")
    check_identity(identity, plan)
    required = {str(Path(plan[key]).resolve()) for key in ("python", "helper", "sql")}
    if not required <= {raw for raw, _ in plan["inputs"]}:
        raise ContainmentFatal("critical_input_binding_missing")
    for raw, expected in plan["inputs"]:
        path = Path(raw)
        if (
            path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ContainmentFatal("input_substitution")
        child_entry = path
        for entry in (path, *path.parents):
            if os.access(entry, os.W_OK, effective_ids=True):
                info = entry.stat()
                sticky_protected = (
                    entry != path
                    and stat.S_ISDIR(info.st_mode)
                    and info.st_mode & stat.S_ISVTX
                    and info.st_uid != plan["client_uid"]
                    and child_entry.stat().st_uid != plan["client_uid"]
                )
                if not sticky_protected:
                    raise ContainmentFatal("caller_writable_host_input")
            child_entry = entry
    roots = {str(Path(raw).resolve()) for raw in sys.path if raw and Path(raw).exists()}
    if roots != set(plan["import_roots"]):
        raise ContainmentFatal("import_root_binding_mismatch")
    for raw in roots:
        root = Path(raw)
        entries = root.rglob("*") if root.is_dir() else iter((root,))
        for entry in entries:
            remaining(plan["cleanup_deadline"] if cleanup_phase else plan["deadline"])
            if entry.is_symlink() or os.access(entry, os.W_OK, effective_ids=True):
                raise ContainmentFatal("caller_writable_or_unbound_import")


def cli_arguments(args: list[str], plan: dict[str, Any]) -> None:
    if not args or args[0] not in ("query", "result") or len(args) > 20:
        raise ContainmentFatal("unapproved_cli_action")
    if args[0] == "query" and args != [
        "query",
        "--file",
        plan["sql"],
        "--intent",
        "enumerate",
        "--view",
        "historical",
        "--page-size",
        "1",
        "--json",
    ]:
        raise ContainmentFatal("unapproved_query_arguments")
    if any(len(arg.encode()) > 4096 for arg in args):
        raise ContainmentFatal("cli_argument_limit")
    if args[0] == "result":
        from uuid import UUID

        UUID(args[1])
        values = args[2:]
        seen: set[str] = set()
        while values:
            if values[0] == "--json":
                if "--json" in seen:
                    raise ContainmentFatal("duplicate_result_flag")
                seen.add(values.pop(0))
                continue
            if (
                len(values) < 2
                or values[0] in seen
                or values[0] not in ("--digest", "--page-size", "--cursor")
            ):
                raise ContainmentFatal("unapproved_result_arguments")
            key, value, *values = values
            seen.add(key)
            if key == "--digest" and (
                len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ContainmentFatal("invalid_result_pin")
            if key == "--page-size" and value != "1":
                raise ContainmentFatal("changed_case_page_size")
        if not {"--digest", "--page-size", "--json"} <= seen:
            raise ContainmentFatal("missing_result_pin")


def child(plan: dict[str, Any]) -> int:
    send(sys.stdout.buffer, {"identity": process(os.getpid())}, plan["deadline"])
    gate = Channel(sys.stdin.buffer, plan["deadline"]).read()
    cli_arguments(gate["args"], plan)
    env = gate["env"]
    if set(env) != {
        "MEMORIESQL_RESULTS_SOCKET",
        "MEMORIESQL_LOCAL_CREDENTIAL",
        "MEMORIESQL_WORKSPACE_ID",
        "MEMORIESQL_STATE_DIR",
    }:
        raise ContainmentFatal("inherited_client_environment")
    os.execve(
        plan["python"],
        [
            plan["python"],
            "-I",
            "-c",
            "from memoriesql.cli import main; raise SystemExit(main())",
            *gate["args"],
        ],
        env,
    )
    raise AssertionError("exec returned")


def worker(plan: dict[str, Any], path: Path, digest: str) -> int:
    state = private_state(plan)
    state.mkdir(mode=0o700)  # Exclusive; never adopt another worker's state.
    send(
        sys.stdout.buffer,
        {
            "identity": process(os.getpid()),
            "state": [state.stat().st_dev, state.stat().st_ino],
        },
        plan["deadline"],
    )
    incoming = Channel(sys.stdin.buffer, plan["deadline"])
    environment = incoming.read()["env"]
    environment["MEMORIESQL_STATE_DIR"] = str(state)
    while True:
        request = incoming.read()
        if request.get("op") == "stop":
            send(sys.stdout.buffer, {"closed": True}, plan["cleanup_deadline"])
            return 0
        cli_arguments(request["args"], plan)
        send(sys.stdout.buffer, {"birth": True}, plan["deadline"])
        if incoming.read() != {"spawn": True}:
            raise ContainmentFatal("birth_not_authorized")
        process_child = subprocess.Popen(
            [plan["python"], "-I", plan["helper"], "child", str(path), digest],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        assert process_child.stdin is not None and process_child.stdout is not None
        ready = Channel(process_child.stdout, plan["deadline"]).read()
        send(sys.stdout.buffer, ready, plan["deadline"])
        if incoming.read() != {"registered": True}:
            raise ContainmentFatal("child_not_registered")
        send(
            process_child.stdin,
            {"args": request["args"], "env": environment},
            plan["deadline"],
        )
        process_child.stdin.close()
        process_child.stdin = None
        reply = cli_reply(process_child.stdout, plan["deadline"])
        code = process_child.wait(timeout=min(10, remaining(plan["deadline"])))
        send(
            sys.stdout.buffer,
            {"code": code, "reply": reply, "reaped": True},
            plan["deadline"],
        )


def cleanup(plan: dict[str, Any]) -> int:
    send(
        sys.stdout.buffer, {"identity": process(os.getpid())}, plan["cleanup_deadline"]
    )
    if Channel(sys.stdin.buffer, plan["cleanup_deadline"]).read() != {
        "registered": True
    }:
        raise ContainmentFatal("cleanup_not_registered")
    for expected in plan["targets"]:
        current = process(expected["pid"])
        if same_process(expected, current):
            check_identity(current, plan)
            fd = os.pidfd_open(expected["pid"])
            try:
                if same_process(expected, process(expected["pid"])):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
            finally:
                os.close(fd)
        while same_process(expected, process(expected["pid"])):
            remaining(plan["cleanup_deadline"])
            time.sleep(0.01)
    state = private_state(plan)
    info = os.lstat(state)
    if (
        (info.st_dev, info.st_ino) != tuple(plan["state_identity"])
        or info.st_uid != plan["client_uid"]
        or not stat.S_ISDIR(info.st_mode)
        or info.st_mode & 0o077
    ):
        raise ContainmentFatal("state_substitution")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ContainmentFatal("unsafe_state_cleanup_platform")
    fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != tuple(plan["state_identity"]):
            raise ContainmentFatal("state_changed_during_cleanup")
        for name in os.listdir(fd):
            remaining(plan["cleanup_deadline"])
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(item.st_mode):
                shutil.rmtree(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)
    finally:
        os.close(fd)
    # S sealed the state parent after registration. Only S removes the empty
    # entry; C cannot substitute that entry while deletion is in progress.
    send(
        sys.stdout.buffer,
        {"cleanup": "known_targets_absent_state_contents_deleted"},
        plan["cleanup_deadline"],
    )
    return 0


class Session:
    """S-side fixed two-invocation controller; all unknown births latch FAIL."""

    def __init__(
        self, plan: dict[str, Any], secret: str, workspace: str, socket_path: str
    ) -> None:
        self.plan = plan
        self.environment = {
            "MEMORIESQL_LOCAL_CREDENTIAL": secret,
            "MEMORIESQL_WORKSPACE_ID": workspace,
            "MEMORIESQL_RESULTS_SOCKET": socket_path,
        }
        self.ledger = Ledger()
        self.monitor: Any = None
        self.channel: Any = None
        self.state_identity: list[int] | None = None
        self.invocations = 0
        self.closed = False

    @classmethod
    def prepare(
        cls, root: Path, secret: str, workspace: str, socket_path: str
    ) -> Session:
        try:
            return cls._prepare(root, secret, workspace, socket_path)
        except ContainmentFatal:
            raise
        except Exception as error:
            raise ContainmentFatal(type(error).__name__ + "_binding_unknown") from None

    @classmethod
    def _prepare(
        cls, root: Path, secret: str, workspace: str, socket_path: str
    ) -> Session:
        # This external binding is NOT supplied or authorized by source prep.
        binding = os.environ.get("N1_TEST_CLI_IDENTITY_BINDING")
        digest = os.environ.get("N1_TEST_CLI_IDENTITY_BINDING_SHA256")
        if not binding or not digest:
            raise ContainmentFatal("fixture_privilege_binding_missing_execution_held")
        plan = read_binding(Path(binding), digest)
        if (
            os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Linux"
        ):
            raise ContainmentFatal("not_dedicated_public_hosted_linux")
        if (
            plan["source_sha"] != os.environ.get("SOURCE_SHA")
            or plan["root"] != "@fresh-fixture"
        ):
            raise ContainmentFatal("fixture_binding_source_or_root_mismatch")
        if "targets" in plan or "state_identity" in plan:
            raise ContainmentFatal("prepopulated_cleanup_targets")
        if plan["service_uid"] != os.geteuid() or plan["gid"] != os.getegid():
            raise ContainmentFatal("fixture_service_identity_mismatch")
        if plan["authority_sha256"] != os.environ.get(
            "N1_TEST_CLI_IDENTITY_AUTHORITY_SHA256"
        ):
            raise ContainmentFatal("fixture_authority_binding_missing_execution_held")
        plan["deadline"] = float(os.environ[NORMAL_ENV])
        plan["cleanup_deadline"] = float(os.environ[CLEANUP_ENV])
        if plan["cleanup_deadline"] - plan["deadline"] != 120:
            raise ContainmentFatal("changed_shared_cleanup_budget")
        remaining(plan["deadline"])
        substitutions = {
            "@installed-python": str(Path(sys.executable).resolve()),
            "@copied-helper": str(Path(__file__).resolve()),
        }
        if plan["python"] != "@installed-python" or plan["helper"] != "@copied-helper":
            raise ContainmentFatal("unapproved_executable_template")
        plan["python"] = sys.executable
        plan["helper"] = substitutions["@copied-helper"]
        plan["inputs"] = [
            (substitutions.get(raw, raw), pin) for raw, pin in plan["inputs"]
        ]
        plan["root"] = str(root)
        plan["nonce"] = uuid.uuid4().hex
        plan["sql"] = str(root / "public-cli.sql")
        plan["inputs"].append(
            (plan["sql"], hashlib.sha256(Path(plan["sql"]).read_bytes()).hexdigest())
        )
        for raw, pin in (
            ("/usr/bin/sudo", plan["sudo_sha256"]),
            ("/usr/bin/env", plan["env_sha256"]),
        ):
            info = os.lstat(raw)
            if (
                info.st_uid != 0
                or info.st_mode & 0o022
                or not stat.S_ISREG(info.st_mode)
                or hashlib.sha256(Path(raw).read_bytes()).hexdigest() != pin
            ):
                raise ContainmentFatal("unbound_system_launcher")
        root.chmod(0o710)
        area = root / "client-area"
        area.mkdir()
        area.chmod(0o730)
        return cls(plan, secret, workspace, socket_path)

    def fail(self, code: str) -> NoReturn:
        self.ledger.unknown(code)

    def launch(self, action: str) -> tuple[Any, Channel]:
        if self.invocations >= 2:
            self.fail("third_run_as_forbidden")
        self.invocations += 1
        path = Path(self.plan["root"]) / (action + ".json")
        data = json.dumps(self.plan, sort_keys=True).encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o440)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        argv = sudo_argv(self.plan, action, path, hashlib.sha256(data).hexdigest())
        self.ledger.birth()
        monitor = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        observed_monitor = process(monitor.pid)
        if observed_monitor is None:
            self.fail("monitor_died_before_registration")
        self.ledger.monitors.append(observed_monitor)
        if action == "run-case":
            self.monitor = monitor
        assert monitor.stdout is not None
        channel = Channel(
            monitor.stdout,
            self.plan["cleanup_deadline"]
            if action == "cleanup"
            else self.plan["deadline"],
        )
        hello = channel.read()
        self.ledger.register(hello["identity"], self.plan)
        if monitor.poll() is not None:
            self.fail("monitor_exited_before_privileged_registration")
        if action == "run-case":
            state = os.lstat(private_state(self.plan))
            if (
                [state.st_dev, state.st_ino] != hello["state"]
                or state.st_uid != self.plan["client_uid"]
                or state.st_mode & 0o077
            ):
                self.fail("state_registration_mismatch")
            self.state_identity = hello["state"]
            (Path(self.plan["root"]) / "client-area").chmod(0o710)
        return monitor, channel

    def start(self) -> None:
        try:
            self.monitor, self.channel = self.launch("run-case")
            send(self.monitor.stdin, {"env": self.environment}, self.plan["deadline"])
        except Exception as error:
            self.fail(type(error).__name__ + "_before_worker_registration")

    def cli(self, args: list[str]) -> tuple[int, dict[str, Any]]:
        try:
            if self.ledger.fatal:
                self.fail("proof_after_fatal")
            if self.monitor.poll() is not None:
                self.fail("worker_or_monitor_died")
            send(self.monitor.stdin, {"op": "cli", "args": args}, self.plan["deadline"])
            if self.channel.read() != {"birth": True}:
                self.fail("child_birth_protocol")
            self.ledger.birth()
            send(self.monitor.stdin, {"spawn": True}, self.plan["deadline"])
            hello = self.channel.read()
            self.ledger.register(
                hello["identity"], self.plan, self.ledger.identities[0]["pid"]
            )
            send(self.monitor.stdin, {"registered": True}, self.plan["deadline"])
            reply = self.channel.read()
            if reply.get("reaped") is not True or same_process(
                self.ledger.identities[-1], process(self.ledger.identities[-1]["pid"])
            ):
                self.fail("cli_reaping_unproven")
            return reply["code"], reply["reply"]
        except Exception as error:
            self.fail(type(error).__name__ + "_during_cli")

    def close(self) -> None:
        if self.closed:
            if self.ledger.fatal:
                raise ContainmentFatal("fatal_cannot_be_cleared_by_cleanup")
            return
        self.closed = True
        if self.monitor is None or self.state_identity is None:
            self.fail("pre_registration_cleanup_unknown")
        try:
            if not self.ledger.fatal:
                send(self.monitor.stdin, {"op": "stop"}, self.plan["cleanup_deadline"])
                if self.channel.read() != {"closed": True}:
                    self.fail("worker_closure_unproven")
                if (
                    self.monitor.wait(timeout=remaining(self.plan["cleanup_deadline"]))
                    != 0
                ):
                    self.fail("first_monitor_failed")
            self.plan["targets"] = self.ledger.identities.copy()
            self.plan["state_identity"] = self.state_identity
            monitor, channel = self.launch("cleanup")
            send(monitor.stdin, {"registered": True}, self.plan["cleanup_deadline"])
            if channel.read() != {
                "cleanup": "known_targets_absent_state_contents_deleted"
            }:
                self.fail("cleanup_unknown")
            if monitor.wait(timeout=remaining(self.plan["cleanup_deadline"])) != 0:
                self.fail("cleanup_monitor_or_state_unclosed")
            if self.monitor.wait(timeout=remaining(self.plan["cleanup_deadline"])) != 0:
                self.fail("first_monitor_not_successfully_reaped")
            for identity in self.ledger.identities:
                if same_process(identity, process(identity["pid"])):
                    self.fail("registered_process_still_present")
            state = private_state(self.plan)
            info = os.lstat(state)
            if [info.st_dev, info.st_ino] != self.state_identity:
                self.fail("state_entry_substitution")
            state.rmdir()
            if self.ledger.fatal or self.ledger.birth_pending:
                self.fail("fatal_cannot_be_cleared_by_cleanup")
        except Exception as error:
            self.fail(type(error).__name__ + "_cleanup_unknown")


def main() -> int:
    action, raw, digest = sys.argv[1:]
    plan = read_plan(Path(raw), digest)
    check_worker(plan, cleanup_phase=action == "cleanup")
    if action == "child":
        return child(plan)
    if action == "run-case":
        return worker(plan, Path(raw), digest)
    if action == "cleanup":
        return cleanup(plan)
    raise ContainmentFatal("unknown_action")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # No credential, environment, provider/server diagnostic or source data.
        raise SystemExit(1) from None
