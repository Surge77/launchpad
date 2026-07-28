# launchpad

Terminal project launcher. Pick a repo, get its **whole** dev environment running — editor, dev servers, sidecar processes — in one keystroke, then tear it all down with one more.

Scans **31 repos in 154 ms** (median of 5 cold runs, Windows 11, NVMe) and auto-derives run commands for **20 of them** with zero configuration. The other 11 are libraries and CLIs with nothing to serve; a 4-line `launcher.toml` covers anything detection can't infer.

```
$ launch
  ▸ collabboard      TS     dev                 ○ —
    relay            Go+TS  gateway + web       ● running
    ledger-engine    Java   bootRun             ○ —
    regin            Py     —                   ○ —

[enter] launch  [e] editor  [s] stop  [/] filter  [r] rescan  [q] quit

→ relay
  ✓ editor C:/Users/tdmne/Desktop/experiments/relay
  ✓ gateway pid 21884       ready
  ✓ web     pid 12312 :3000 ready
```

## Install

```sh
git clone https://github.com/Surge77/launchpad && cd launchpad
python -m venv .venv && .venv/Scripts/activate     # source .venv/bin/activate on Unix
pip install -e ".[dev]"
```

Point it at wherever your repos live:

```sh
setx LAUNCHPAD_ROOT "C:\Users\you\Desktop\experiments"    # or --root on every call
```

## Usage

| Command | Does |
|---------|------|
| `launch` | TUI: browse, filter, launch, stop |
| `launch ls` | List every project, its stack, its tasks, and whether it's running |
| `launch up <name>` | Launch by name (partial match works: `launch up collab`) |
| `launch ps` | Show every process launchpad is tracking |
| `launch stop [name]` | Stop one project, or everything if name is omitted |
| `launch logs <name>` | Print log file paths for a project's tasks |

Flags: `--no-editor` starts processes only, `--no-wait` skips port readiness polling.

## Detection

No config needed when the repo follows convention:

| Marker | Command | Port |
|--------|---------|------|
| `package.json` with `dev`/`start`/`serve` | `pnpm dev` / `yarn dev` / `bun dev` / `npm run dev` (from the lockfile) | 3000 Next, 5173 Vite |
| `pom.xml` with spring-boot | `mvn spring-boot:run` | 8080 |
| `build.gradle[.kts]` | `gradlew bootRun` | 8080 |
| `Cargo.toml` | `cargo run` | — |
| `go.mod` | `go run ./...` | — |
| `pyproject.toml` / `requirements.txt` with fastapi / django / flask | `uvicorn` / `runserver` / `flask run` | 8000 / 8000 / 5000 |

Monorepos are handled: a repo whose root has no manifest gets its immediate subdirectories scanned, and each becomes its own task named after the directory — `relay` comes out as `Go+TS` with `gateway + web`.

## `launcher.toml`

Drop this in any repo to override detection completely. Tasks start in listed order.

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
open = true                # open localhost:3000 once the port accepts connections
env  = { NEXT_PUBLIC_API = "http://localhost:8080" }
```

A task with a `port` blocks until that port accepts a connection (30 s cap) before the next line prints, so `open = true` never races a half-booted server.

## Design notes

**Process identity, not just PIDs.** State records `(pid, create_time)`. After a hard crash the OS can hand a dead server's PID to something unrelated, and a PID-only `stop` would kill a stranger; matching creation time makes the check exact. Dead entries are pruned from disk on every read.

**No shell.** Task commands come from a repo's `launcher.toml`, so they never reach a shell — `shell=True` would let `pnpm dev && anything` run a second command. `argv[0]` is resolved through `shutil.which()`, which is also what makes Windows `.cmd` shims (`pnpm`, `gradlew`) work without one. `cwd` is rejected if it's absolute or contains `..`, checked against both POSIX and Windows path flavours (`Path("/etc").is_absolute()` is `False` on Windows).

**Full-tree teardown.** `stop` walks children recursively, sends `terminate`, waits 5 s, then `kill`s survivors — a `next dev` parent exiting without its worker leaves an orphan holding port 3000.

Logs stream to `%LOCALAPPDATA%\launchpad\logs\<project>-<task>.log`; state lives beside them in `state.json`.

## Tests

```sh
python -m pytest --cov=launchpad --cov-report=term-missing
python -m pyright
```

**80 tests, 81% line coverage, 0 pyright errors.** Non-UI modules run 82–99%; `tui.py` sits at 30% by design — Textual widget interaction is covered by hand, not by unit tests. The launch → probe → stop cycle is verified end-to-end against a real HTTP server binding a real port.
