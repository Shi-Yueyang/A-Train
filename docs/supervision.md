# Process Supervision, Health Monitoring, and Cross-Host Control

This document records the design discussion that re-scoped
[`ashley.md`](ashley.md). It answers three questions:

1. Do we need a custom supervisor (Ashley) for a multi-process,
   multi-host cab?
2. How is unit health monitored in real time across machines?
3. How does a button in a web UI start a process on another host?

Conclusion up front: **the OS already owns lifecycle; we do not build a
supervisor.** The remaining custom surface is a thin control-plane shim
(whitelisted remote start/stop/status), and even that can be zero-code with
off-the-shelf products at the cost of buttons living in their UI instead of
ours.

---

## 1. Why not Ashley as designed

`ashley.md` specified a per-cab supervisor: spawn/watch/start/stop FSM,
process-tree kill, event journal, cross-host peer gossip. Audited against
requirements:

| Ashley feature                 | Where it actually belongs                                        |
| ---------------------------------- | ---------------------------------------------------------------- |
| Kill whole process tree            | systemd cgroups (`KillMode=control-group`), SCM job semantics    |
| Event journal                      | journald / Windows System event log / Task Scheduler history     |
| Peer federation, state gossip      | unnecessary — redial convergence (ashley.md principle 5) makes start order and restarts invisible |
| Manifest + port placeholders       | keep the manifest as a file; drop the templating engine          |

`remote shell` covers the imperative start case; what it cannot do is
declarative supervision — that is exactly the native supervisors' job, so the
need for a custom lifecycle engine is void. What survives of Ashley is
its **principles** (convergence over ordering, sole control surface,
no simulation authority, deletion-safety) and its read-only **rollup idea**
(§7), not its engine.

## 2. Lifecycle: use the OS supervisors

### 2.1 Non-daemon processes

systemd **prefers** plain foreground processes (`Type=simple`, the default).
"Daemon" in the double-fork sense is the awkward case, not the easy one:

- Self-forking/detaching app: `Type=forking` + `PIDFile=`, or `Type=notify`
  with `sd_notify(READY=1)`.
- Liveness is keyed to the main PID: if a launcher parent exits while
  children stay alive, systemd treats the unit as stopped and kills the
  cgroup. Restructure the exec, or use `Type=forking`.
- Children spawned by the unit belong to the same cgroup; stop removes the
  whole tree.
- Hangs are undetectable without cooperation: `WatchdogSec=` requires the
  process to call `sd_notify(WATCHDOG=1)`. "Alive but frozen" stays a
  non-goal at OS level; use an app-level ready probe (§4.2) instead.

### 2.2 Environment constraints decide the supervisor, not daemon-ness

| Process needs                        | Linux                                                     | Windows                                   |
| ------------------------------------ | --------------------------------------------------------- | ----------------------------------------- |
| Headless / console                   | system unit: `Restart=always`                             | WinSW service (`RestartOnFailure`)        |
| Visible desktop / interactive session | user unit: `systemctl --user` + `loginctl enable-linger <user>` (reaches X/Wayland via `XDG_RUNTIME_DIR`) | **Task Scheduler**: "run only when user is logged on", "restart if fails" — a session-0 service cannot show a window |

Win300C's rule: headless-capable → WinSW; must show its window → Task
Scheduler on an auto-logon dev account. Health monitoring must then read the
user manager (Linux) or task history (Windows) as well as the system manager.

### 2.3 Registration work (config, not code)

Per unit: write the systemd unit / WinSW XML / scheduled task; enable linger
where user units run; open the firewall ports of the agents (§5).

## 3. The hard constraint for cross-host control

> A browser cannot start a process on another machine. Something must already
> be listening on that host.

So "button on a-train UI → remote process runs" is always
`UI → resident listener on target host → OS supervisor`, and the only real
choice is what the listener is:

- **Mechanism A — thin per-host HTTP agent** (recommended if we build it):
  FastAPI, runs as an OS service, `POST /units/{id}/start|stop|restart` maps
  unit IDs through the manifest to a **whitelisted argv**
  (`systemctl start <name>` / `sc start <name>` / `schtasks /run`). It starts
  nothing itself — it only toggles pre-registered units. Idempotent; bearer
  token; CORS for the a-train origin; logs every call.
- **Mechanism B — reuse sshd**: a backend (never the browser, never a-train)
  runs `ssh host start-unit <id>` with a dedicated key and
  `ForceCommand`/`RestrictedShell` wrapper. Zero new daemons; you hand-roll
  status queries, timeouts, and Windows quirks instead.
- **Off-the-shelf**: Zabbix "global scripts" give per-host buttons +
  permissioning inside Zabbix's own UI; Cockpit gives per-host service
  control on Linux only. No off-the-shelf product rolls a multi-host cab
  up into one view with our semantics.

**Boundary rule:** this never goes inside a-train. Remote process control is
not simulation state and violates the domain/simulation wall-clock-free
design; the web UI calls the control-plane endpoint (a different origin)
directly and degrades to read-only when it is unreachable.

## 4. Health model

### 4.1 Two signals, never conflated

- **`alive`** — OS level: unit active?, MainPID, `NRestarts`, exit code.
- **`ready`** — app level: the unit's own status endpoint. Already
  implemented for the simulator's view of ATP links:
  `GET /api/atp/status` (listener READY/STOPPED plus active peer count) — the cross-host link edges
  are app-plane facts, and no supervisor knows them.

Process `alive` ≠ healthy (hung-but-alive reports UP); that gap belongs to
`ready` probes, not to a lifecycle engine.

### 4.2 Native real-time detection (per machine)

| Host OS | Push source                                                        | Pull source                             |
| ------- | ------------------------------------------------------------------ | --------------------------------------- |
| Linux   | `systemctl subscribe` (D-Bus `PropertiesChanged`/`JobNew`), `journalctl -u <unit> -f -o json` — system **and** user buses | `systemctl show <u> -p ActiveState,SubState,NRestarts,ExecMainExitStatus` |
| Windows | System event log 7031/7034 (service crash) and Task Scheduler event history | `sc query <svc>` / `Get-ScheduledTaskInfo` ~1s poll |

### 4.3 The gap: aggregation and history

No OS provides cross-host aggregation or retention. Fill it either with
config-only tooling or a small custom layer (§5, §6):

- Prometheus + Grafana: `node_exporter` systemd collector and
  `windows_exporter` service/task collectors give one live pane, alert
  rules, and history for all hosts — no code. 1–15 s scrape latency is the
  practical definition of "real time".
- A custom collector gives the same view plus cab semantics (rollup,
  unit IDs, buttons in our UI) — a few hundred lines.

## 5. Custom build list (only if the zero-code path is not enough)

Ordered smallest-first; steps 1–2 already eliminate manual restarts and
produce one live screen.

1. **Manifest** (a file, loaded by the agent and the aggregator):
   ```json
   unit := { id, host, supervisor: systemd|user-unit|winsw|taskscheduler,
             service_name, ready_url?, required: bool }
   ```
   No port templating, no dependency DAG — convergence over ordering.
2. **Per-host agent** (~500–800 lines): §3 Mechanism A + read side (§4.2
   normalized to `{alive, ready, NRestarts}`) + `GET /events` WebSocket
   (delta push, full-sync on connect, bounded queue/oldest-dropped — the
   same semantics as a-train `/ws`). Itself supervised with `Restart=always`.
3. **Aggregator** (~200 lines, optional at first): dials every agent's
   `/events`, merges `host → unit → state`, computes rollup (all
   `required` alive+ready ⇒ GREEN), relays unit commands to the owning
   agent. Gives automation a stable address; skippable while clients open N
   sockets.
4. **UI**: health panel + start/stop buttons in the a-train web client,
   calling the agent/aggregator (CORS + token; hide controls when
   unreachable).
5. **Policies**: keep automation = plain API calls. Only encode: surface
   rising `NRestarts` as RED (no auto-fix); link repair stays entirely on
   redial/re-publish semantics.

Deferred/not built: alerting engine beyond Prometheus, schedules,
dependency-ordered restarts, log analysis, hang detection inside units.

## 6. Zero-code option matrix

| Path                                   | Code | Delivers                                                     | Gaps for us                                        |
| -------------------------------------- | ---- | ------------------------------------------------------------ | -------------------------------------------------- |
| Pure OS (systemd + WinSW/Task Scheduler) | 0  | restarts, boot start, per-host CLI status                    | no cross-host pane, no web buttons                 |
| Prometheus + Grafana                    | 0 (config) | cross-host live pane, alerts, history                     | read-only; buttons elsewhere                       |
| Zabbix global scripts                   | 0 (config) | pane + alerts + remote start/stop buttons                | buttons live in Zabbix, not the cab UI; heavyweight for a dev cab |
| Thin agent + aggregator (§5)            | 200–800 lines | everything above + our UI is the control plane, cab rollup semantics | we own the code                          |

Recommended default: **Prometheus+Grafana for monitoring (never hand-roll
dashboards/history), and cap the custom-code ambition at a few hundred lines
for the buttons** — or accept a separate product's UI and write nothing.

## 7. Status of `ashley.md`

Superseded in substance by this document: its lifecycle engine, process
runner, peer federation, and journal are replaced by native supervisors plus
the thin shim in §5. Its principles 2–5 (deletion-safe,
no simulation logic, convergence over ordering, hand-written unit configs)
and its control-API shape (§8) still describe the remaining shim. Kept for
history; §5 is the implementable version.
