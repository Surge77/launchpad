# Configuration reference

Two layers: environment variables control launchpad itself, `launcher.toml` controls a single project.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAUNCHPAD_ROOT` | `~/Desktop/experiments` | Directory scanned for git repos. `--root` overrides per call. |
| `LAUNCHPAD_EDITOR` | `code` | Editor command. Beats a project's `editor` setting. |
| `LAUNCHPAD_HOME` | `%LOCALAPPDATA%\launchpad` / `~/.local/share/launchpad` | Where state, usage, and logs are written. Handy for isolating test runs. |

## `launcher.toml`

Place it in a repository root. Its presence disables all auto-detection for that repo — you get exactly the tasks you declare, in the order you declare them.

### Top level

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `editor` | string | `code` | Editor to open for this project. `LAUNCHPAD_EDITOR` still wins. |
| `task` | array of tables | — | The tasks to run. Order is start order. |

### `[[task]]`

| Key | Type | Required | Meaning |
|-----|------|----------|---------|
| `name` | string | yes | Label in the UI, log filename, and `launch ps` output. Must be unique. |
| `cmd` | string | yes | Command to run. Split with `shlex`, executed **without a shell**. |
| `cwd` | string | no | Directory relative to the repo root. Must stay inside it — absolute paths and `..` are rejected. |
| `port` | integer 1–65535 | no | Port this task listens on. Enables pre-flight conflict checks and readiness waiting. |
| `open` | boolean | no (`false`) | Open `http://localhost:<port>` once the port accepts connections. Requires `port`. |
| `env` | table of strings | no | Extra environment variables, merged over the inherited environment. |

### Validation

Configuration errors fail loudly rather than being silently ignored:

- missing or empty `name` / `cmd`
- duplicate `name` within one file
- `port` that is not an integer in 1–65535 (`true` and `"3000"` are rejected)
- `cwd` that is absolute or escapes the repo
- non-string values in `env`
- malformed TOML

## Examples

### Single Next.js app

```toml
[[task]]
name = "web"
cmd  = "pnpm dev"
port = 3000
open = true
```

### Backend + frontend, ordered

`api` must be listening before `web` starts, so give `api` a port — the next task waits for it.

```toml
[[task]]
name = "api"
cmd  = "mvn spring-boot:run"
cwd  = "services/api"
port = 8080

[[task]]
name = "web"
cmd  = "pnpm dev"
cwd  = "apps/web"
port = 3000
open = true
env  = { NEXT_PUBLIC_API_URL = "http://localhost:8080" }
```

### Infrastructure first

```toml
[[task]]
name = "infra"
cmd  = "docker compose up postgres redis"

[[task]]
name = "api"
cmd  = "uvicorn app.main:app --reload"
port = 8000
```

### Python CLI with a test watcher

Projects with nothing to serve still benefit — `launch` opens the editor and starts the watcher.

```toml
[[task]]
name = "tests"
cmd  = "pytest -f"
```

### Rust workspace with a web UI

```toml
[[task]]
name = "engine"
cmd  = "cargo run --release --bin engine"
port = 9001

[[task]]
name = "ui"
cmd  = "pnpm dev"
cwd  = "ui"
port = 5173
open = true
```

## Generated configuration (`launch new`)

Scaffolded projects arrive with a `launcher.toml` already written, so `launch up <name>` works immediately.

| Stack | Generated tasks | Ports |
|-------|-----------------|-------|
| `next` | `web` → `pnpm dev` | 3000, opens browser |
| `mern` | `api` → `npm run dev` (server/), then `web` → `npm run dev` (client/) | 5000, then 5173 opening the browser |
| `spring` | `api` → `mvn spring-boot:run` | 8080 |
| `fastapi` | `api` → `uv run uvicorn app.main:app --reload` | 8000, opens browser |

For `mern` the ordering matters: `api` is listed first and carries a port, so the client only starts once the API is actually accepting connections.

Each also gets `.env.example`, `docker-compose.yml` for its datastore, a README, and a git repository with one commit.

## Auto-detection rules

Used only when no `launcher.toml` exists. Root manifests are checked first; if none are found, immediate subdirectories are scanned and each becomes a task named after its directory.

| Detected file | Condition | Command | Port |
|---------------|-----------|---------|------|
| `package.json` | has script `dev`, `start`, or `serve` | package manager from lockfile (`pnpm-lock.yaml` → `pnpm`, `bun.lockb` → `bun`, `yarn.lock` → `yarn`, else `npm run`) | 3000 if Next, 5173 if Vite |
| `pom.xml` | contains `spring-boot` | `mvn spring-boot:run` | 8080 |
| `pom.xml` | otherwise | `mvn -q compile` | — |
| `build.gradle[.kts]` | — | `gradlew bootRun` (`.bat` on Windows) | 8080 |
| `Cargo.toml` | — | `cargo run` | — |
| `go.mod` | — | `go run ./...` | — |
| `pyproject.toml` / `requirements.txt` | mentions `fastapi` | `uvicorn main:app --reload` | 8000 |
| | mentions `django` | `python manage.py runserver` | 8000 |
| | mentions `flask` | `flask run` | 5000 |

Stack labels come from the same markers: `tsconfig.json` present means `TS`, otherwise `JS`. A monorepo shows its parts joined, e.g. `Go+TS`.

Directories named `node_modules`, `target`, `dist`, `build`, `__pycache__`, `.venv`, `venv`, or `.git` are never scanned.

If detection finds nothing runnable, `launch ls` shows `—` and `launch doctor` tells you to add a `launcher.toml`.
