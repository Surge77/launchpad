# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semantic](https://semver.org/).

## [0.1.0] — 2026-07-28

First release.

### Added

- **Discovery** — scans a root directory two levels deep for git repos and infers run commands from `package.json` (package manager chosen by lockfile), `pom.xml`, `build.gradle[.kts]`, `Cargo.toml`, `go.mod`, `pyproject.toml`, and `requirements.txt`.
- **Monorepo support** — repos without a root manifest get immediate subdirectories scanned, each becoming a task named after its directory (`relay` → `Go+TS`, `gateway + web`).
- **`launcher.toml`** — per-repo override for tasks, working directory, ports, browser opening, environment variables, and editor, with strict validation.
- **Pre-flight checks** — verifies executables are on PATH, working directories exist, and ports are free, naming the process that holds a conflicting port. Warns when a `.env.example` has no matching `.env`. Errors block launch unless `--force`.
- **TUI** (Textual) — filterable project list with stack, tasks, run state, and git branch; live per-task log streaming; keys for launch, editor-only, stop, restart, doctor, force launch, rescan.
- **CLI** — `ls` (with `--git` and `--json`), `up` (multiple projects, `--no-editor`, `--no-wait`, `--force`), `restart`, `stop`, `ps` (`--json`), `doctor`, `logs` (`-f`, `-n`).
- **Process tracking** — persisted `(pid, create_time)` pairs so PID reuse cannot cause the wrong process to be killed; dead entries pruned on every read.
- **Recursive teardown** — terminates the full process tree, waits 5 s, then kills survivors.
- **Safe restart** — waits for ports to actually release before relaunching.
- **Frecency ordering** — projects launched often and recently sort to the top.
- **Parallel git status** — branch, dirty count, and ahead/behind across all repos via a thread pool.

### Platform notes

- Busy ports are detected without elevated privileges via a TCP probe. Identifying *which* process holds a port needs root on macOS; when it can't be identified, launchpad reports that plainly instead of treating the port as free.
- `--force` frees ports owned by servers launchpad started on any platform (using its own tracked PIDs) and refuses to guess at an unidentifiable foreign holder.
- Child processes inherit `PYTHONUNBUFFERED=1` so Python dev-server output reaches logs immediately.
- CI verifies Linux, macOS, and Windows on Python 3.11 and 3.12.

### Security

- Task commands never reach a shell: `shlex` splitting plus `shutil.which` resolution of `argv[0]`, which also makes Windows `.cmd` shims work without `shell=True`.
- `cwd` values that are absolute or contain `..` are rejected, validated against both POSIX and Windows path semantics.
- Port-freeing is destructive and reachable only behind an explicit opt-in flag.

[0.1.0]: https://github.com/Surge77/launchpad/releases/tag/v0.1.0
