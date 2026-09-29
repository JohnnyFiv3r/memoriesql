# Trusted query host (`memoriesql broker`)

Status: implementation in this development branch, part of the unpublished,
amended `memoriesql.core-cli.v1` record. No release is claimed. Agent query
access through a host is available only on an installation whose isolation has
been qualified as described below; until then it stays unavailable.

## Why it exists

`memoriesql.agent-sql-results.v1` §2 requires one trusted executor that owns
both database connection classes, trusted bookkeeping and the restricted reader.
Agents receive neither credential nor a general database client, and that
isolation must hold against the agent's actual shell, filesystem and process
capabilities. Running the executor inside the agent's own CLI process cannot meet
that requirement: the process would need the database URLs in its environment.

The trusted query host is that executor as a separate local process, running as
a dedicated service account. Agents reach it only through a Unix socket and
present their own paired credential in each request; the host authenticates
exactly that principal. The agent's environment holds only the socket path, its
workspace id, its own expiring paired secret and a state directory.

## What it does and does not do

- **Serves:** `start_run`, `handle` (the contract's `query` and `reuse_result`
  request bytes), and the existing `inspect`, `source` and `relations` readers.
  Request and reply bytes pass through unchanged; the host adds no semantics.
- **Admits only:** connections whose kernel-reported peer uid is the configured
  client uid, and requests that present a current paired `agent` principal of the
  configured workspace. The host hashes the presented secret itself; no principal
  id is ever taken from the caller. Every refusal (unknown, expired, revoked,
  non-agent or other-workspace credential) is the same non-disclosing
  `unavailable` reply, and none reaches the executor.
- **Never serves over the socket:** closing a run, provisioning, cleanup or any
  other owner or operator action. Those are operator commands that run only as
  the service account (below).
- **Owner-only readers stay owner-only.** The existing `inspect`, `source` and
  `relations` readers require raw-source authority that paired agents cannot
  hold, so through the host they return the same `unavailable` an agent would
  get anywhere. Agents cite through their own query results.

## Wire protocol (`PROTOCOL_VERSION = 1`)

Every frame is a four-byte big-endian length followed by that many bytes, at
most 1 MiB. A request is a header frame, closed JSON
`{"credential","op","v":1,"workspace_id"}` with `op` one of `start_run`,
`handle`, `inspect`, `source`, `relations`, followed by a body frame: the
contract request bytes, verbatim (empty for `start_run`). The reply is one
frame. Malformed, oversized, late (10 s to receive a request) or unknown frames
receive the executor's canonical `unavailable` bytes and nothing else.

The client proves the host before sending anything: the socket's directory must
belong to another uid, not be writable by group or others, and the kernel must
report that same uid as the listening process. A socket created by the caller's
own uid, or anything it can run, is refused before a byte is sent, so an
impostor socket cannot harvest credentials.

## Installation layout (macOS)

| Path | Owner:group | Mode | Contents |
| --- | --- | --- | --- |
| `/usr/local/var/memoriesql-broker` | `root:wheel` | 0755 | Installation root (and `/usr/local/var`) |
| `…/venv` | `root:wheel` | 0755/0644 | Code: a root-owned venv on a root-owned interpreter |
| `…/config` | `_memoriesql:_memoriesql` | 0700 | `broker.json` (0600): both logins, workspace, client uid, profile pin |
| `…/state` | `_memoriesql:_memoriesql` | 0700 | Run registry (for operator close) and last cleanup record |
| `…/log` | `_memoriesql:_memoriesql` | 0700 | Host log (no credentials, digests or content) |
| `…/run` | `_memoriesql:<client group>` | 0750 | `broker.sock` (0660) |
| `/Library/LaunchDaemons/local.memoriesql.query-host.plist` | `root:wheel` | 0644 | Service definition |

The code tree is root-owned so that neither the client uid nor the service
account can modify what the service executes. The interpreter must also be one
the client uid cannot modify: the python.org framework build is `root:wheel`;
Homebrew or per-user interpreters are owned by the client user and are not
acceptable. `broker check` verifies that no path, ancestor or symlink target of
the code, configuration, state or socket directory is writable by the client
uid (mode bits; the installation qualification additionally probes writes).

## Operator procedure

All privileged commands below are run by the owner in their own terminal, never
by an agent. `<cli>` is
`/usr/local/var/memoriesql-broker/venv/bin/memoriesql`, and `<config>` is
`/usr/local/var/memoriesql-broker/config/broker.json`.

1. **Service account and client group.** Choose unused ids below 500 and verify
   them with `dscl . -list /Users UniqueID` and `dscl . -list /Groups
   PrimaryGroupID`. Create group `_memoriesql`, group `_memoriesqlc` and hidden
   user `_memoriesql` (shell `/usr/bin/false`, home `/var/empty`, password `*`),
   then add the client user to `_memoriesqlc` with `dseditgroup`. Group
   membership takes effect for new login sessions.
2. **Directories** with `/usr/bin/install -d` and the owners and modes above.
3. **Code.** Create the venv as root from the root-owned interpreter, verify the
   exact wheel and lock with `/usr/bin/shasum -a 256` against the recorded
   release or candidate hashes, and install with
   `python -I -m pip install --no-cache-dir --require-hashes --only-binary :all:`.
   Never install from a path the client user can modify without verifying it.
4. **Provision** as the service account:
   `sudo -H -u _memoriesql <cli> broker provision --config <config>
   --workspace-id <uuid> --client-uid <uid> --database-host <host>
   --database-port <port> --database-name <db>`. It prompts, without echo, for a
   database administrator URL that is used once and never stored. It generates
   both passwords (256-bit), stores them only in the 0600 configuration and sends
   the server only SCRAM-SHA-256 verifiers. It creates:
   - the control login `memoriesql_query_host`: `LOGIN INHERIT NOSUPERUSER
     NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION`, an INHERIT and SET
     member of `memoriesql_application`, with
     `ALTER DEFAULT PRIVILEGES FOR ROLE … REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`;
   - the restricted reader `memoriesql_query_reader`, through PR-05's reviewed
     provisioning; and grants the reader role to the control login `WITH INHERIT
     TRUE, SET FALSE`, so the host can observe, cancel and confirm the end of its
     own reader backends (and resolve the query schemas by name);
   - the pinned `profile_sha256` of the reviewed reader profile.
5. **Service.** Install the plist below as `root:wheel` 0644, then
   `sudo launchctl bootstrap system /Library/LaunchDaemons/local.memoriesql.query-host.plist`.
6. **Check:** `sudo -H -u _memoriesql <cli> broker check --config <config>`
   (JSON; exit 0 only when every check passes).

The service definition:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.memoriesql.query-host</string>
  <key>UserName</key><string>_memoriesql</string>
  <key>GroupName</key><string>_memoriesql</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/var/memoriesql-broker/venv/bin/python3</string>
    <string>-I</string>
    <string>-m</string>
    <string>memoriesql.cli</string>
    <string>broker</string>
    <string>serve</string>
    <string>--config</string>
    <string>/usr/local/var/memoriesql-broker/config/broker.json</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/bin:/bin</string>
    <key>HOME</key><string>/var/empty</string>
  </dict>
  <key>WorkingDirectory</key><string>/usr/local/var/memoriesql-broker/state</string>
  <key>Umask</key><integer>63</integer>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ExitTimeOut</key><integer>120</integer>
  <key>StandardOutPath</key><string>/usr/local/var/memoriesql-broker/log/host.log</string>
  <key>StandardErrorPath</key><string>/usr/local/var/memoriesql-broker/log/host.log</string>
</dict>
</plist>
```

`serve` refuses to start (exit 2, one JSON log line naming the check) unless the
configuration is a 0600 file owned by the service account in a 0700 directory;
the control login is not a superuser, holds no elevation it never uses and
inherits `memoriesql_application`; it inherits its reader role; the reviewed
reader profile qualifies to exactly the pinned `profile_sha256`; the reader login
is itself; and the socket directory is the service account's, 0750 or stricter.
`KeepAlive` restarts the host whenever it exits unsuccessfully, at most every
30 seconds, so a host started before its database is reachable (for example
after a reboot) comes up once the database does; a refusal is logged on each
attempt until the reported condition is fixed. A draining stop exits 0 and is
not restarted.

### Day-to-day operations

| Task | Command (owner's terminal) |
| --- | --- |
| Restart | `sudo launchctl kickstart -k system/local.memoriesql.query-host` |
| Stop | `sudo launchctl bootout system/local.memoriesql.query-host` (the host stops accepting and drains in-flight requests for up to 90 s) |
| Check | `sudo -H -u _memoriesql <cli> broker check --config <config>` |
| List host-started runs | `sudo -H -u _memoriesql <cli> broker runs --config <config>` (no credential digests are printed) |
| Close a run early | `sudo -H -u _memoriesql <cli> broker close-run --config <config> --run-ref <uuid>`: PR-05's owner close for the run's own principal. Refused while any of its work is unsettled; refunds nothing; the run never reopens. |
| Rotate both logins | `sudo -H -u _memoriesql <cli> broker provision --config <config> --rotate`, then restart. The new secrets are durable in the configuration before the roles change. |
| Revoke an agent | Revoke its pairing grant (`memoriesql clients revoke`, an owner operation). The next request is refused. |
| Run expiry cleanup now | `sudo -H -u _memoriesql <cli> broker cleanup --config <config>` |
| Logs | `sudo cat /usr/local/var/memoriesql-broker/log/host.log` |

**Expiry cleanup.** PR-05's 24-hour content-cleanup deadline holds only while
the host runs it. `serve` runs `cleanup_expired()` at start and hourly, and
records each noncontent outcome in its private state. `broker check` fails when
the last cleanup is older than two hours, failed or reported failures, and when
PR-05's `cleanup_status()` for the workspace shows anything overdue. A run is
refused (`budget_exhausted` / `settlement`) once cleanup is more than 24 hours
overdue.

**Crash recovery.** Before every run or query admission the host runs PR-05's
recovery. A delivery whose owning session ended is settled only once its
restricted reader backend and transaction are confirmed gone, charged at least
its full reservation; nothing is rerun, refunded or disclosed, and an exact
redelivery of the step resolves from committed state. Losing the host process or
only its owner session therefore never frees run or workspace capacity while
database work is still executing.

## Owner authority

The owner's own credential, like both host logins, must never be readable by the
client uid, because the agent runs as that uid. Keep the owner credential and any
owner-capable database URL only in files owned by the service account, and run
owner operations (`init`, `clients pair`/`revoke`, source enrollment and grants)
as the service account with `sudo -H -u _memoriesql`, from the owner's own
terminal. The agent's environment is never derived from that terminal.

## Operator-mediated use

An agent's commands can also be run by the owner, from the owner's own terminal,
under the agent's paired identity: the agent writes a SQL file, the owner runs
`memoriesql query` (or `result`, `schema`) in client mode with only
`MEMORIESQL_RESULTS_SOCKET`, `MEMORIESQL_WORKSPACE_ID`, `MEMORIESQL_STATE_DIR`
and the agent's own `MEMORIESQL_LOCAL_CREDENTIAL` set (read from the agent's
secret file, never typed on a command line), and only the command's output
returns to the agent. Every request is authorized as exactly that paired agent;
the owner's own credential is refused on the socket. This needs no agent
harness qualification, because the agent executes nothing itself.

## Agent harness

The host is necessary but not sufficient: the agent's own harness must be unable
to reach anything that holds a database credential, including the database's
administrator credentials (for example a container runtime's API or
configuration) and the owner's files. For Claude Code, the qualified harness is:

- a root-owned launcher that starts a root-owned copy of the `claude` binary,
  pinned by hash with auto-update disabled, from the agent's working directory,
  with `env -i` plus only `HOME`, `USER`, `LOGNAME`, `SHELL`, a minimal `PATH`
  (the host venv's `bin` and system directories), `LANG`, `TERM`,
  `DISABLE_AUTOUPDATER=1`, `MEMORIESQL_RESULTS_SOCKET`,
  `MEMORIESQL_WORKSPACE_ID`, `MEMORIESQL_STATE_DIR` and the agent's own
  `MEMORIESQL_LOCAL_CREDENTIAL`;
- the flags `--restricted` (only managed settings and `--settings` load, so no
  user or project settings file can widen the policy), `--safe-mode` (no
  plugins, hooks, MCP servers or CLAUDE.md), `--strict-mcp-config` with no MCP
  configuration, and a root-owned `--settings` file:

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "allowUnsandboxedCommands": false,
    "autoAllowBashIfSandboxed": true,
    "filesystem": {
      "denyRead": ["~/"],
      "allowRead": ["<agent working directory>"]
    },
    "network": {
      "allowUnixSockets": ["/usr/local/var/memoriesql-broker/run/broker.sock"],
      "allowedDomains": [],
      "strictAllowlist": true,
      "allowLocalBinding": false
    }
  },
  "permissions": {
    "blockReadsOutsideWorkingDirectories": true,
    "deny": ["WebFetch", "WebSearch"]
  },
  "disableAllHooks": true,
  "enableAllProjectMcpServers": false
}
```

A harness is qualified only by running value-free confinement checks inside
that exact harness on the actual installation: environment and process-table
digest scans, reads of the owner's credential-bearing files, connections to the
container runtime socket, to database ports and to the network, writes outside
the working directory, the unsandboxed-retry refusal and inter-process channels.
The checks print outcomes and digest-match counts only, never values. A harness
that fails any check leaves agent query access unavailable.

## Honest limits

- The agent still runs as the owner's OS user. The guarantee holds only when the
  owner's environment keeps no other copy of the host's secrets or the owner's
  credential anywhere that user can read (files, environment, shell history,
  process table, keychain), and the harness is configured and qualified as above.
  That operator condition is checked (`broker check`, the harness confinement
  checks), not assumed; it must be rechecked after any change to the host, the
  harness or the owner's environment.
- A holder of the raw reader login could change `USERSET` settings; the packet
  states this limitation. Database privileges independently prevent writes,
  elevation and scope widening even when admission is bypassed.
- Peer-uid checks prove which OS user connected, not which program; any process
  the client user runs can use the socket with its own paired credential and
  gets exactly that principal's authority.
- Stock PostgreSQL shows the host only its own sessions and the reader sessions
  it inherits; `pg_hba` must require SCRAM for every TCP login and never `trust`
  (`broker check` verifies that both host logins refuse a passwordless login).
