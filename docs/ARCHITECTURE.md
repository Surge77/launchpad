# Architecture

## Module map

```
cli.py          argument parsing + dispatch only
  └─ commands.py    command implementations (ls/up/restart/stop/ps/doctor/logs)
       ├─ discovery.py   filesystem scan → Project list
       │    └─ config.py      Project/Task model, launcher.toml parsing + validation
       ├─ preflight.py   pre-launch checks (PATH, cwd, ports, .env)
       ├─ runner.py      spawn / wait / restart / teardown
       │    └─ state.py      persisted (pid, create_time) records
       ├─ usage.py       frecency ordering
       ├─ gitinfo.py     parallel git status
       └─ logs.py        tail + incremental follow
tui.py          Textual app; consumes the same modules as commands.py
```

Dependency direction is strictly downward. `config.py` imports nothing from the package; `cli.py` imports only `commands`. Nothing imports `tui.py` except the lazy import inside `cli._dispatch`, which keeps subcommand startup free of Textual's import cost.

## Data flow of a launch

```
launch up relay
  │
  ├─ discovery.discover(root)         walk 2 levels, find .git, read manifests
  ├─ usage.order_projects(...)        frecency sort
  ├─ commands.find(root, "relay")     exact match, else unique substring
  │
  ├─ preflight.check_project(project)
  │    ├─ shutil.which(argv[0])            executable resolvable?
  │    ├─ (path / subdir).is_dir()         cwd exists?
  │    ├─ psutil.net_connections()         port free? if not, who holds it?
  │    └─ .env.example without .env?       warn
  │    → any level=="error" aborts unless --force
  │
  ├─ runner.open_editor(project)
  └─ for each task, in declared order:
       ├─ runner.resolve_argv(cmd)         shlex split + which, shell=False
       ├─ subprocess.Popen(argv, cwd, env, stdout→logfile, new process group)
       ├─ state.add(pid, create_time, ...) persist identity
       ├─ wait_for_port_open(port)         poll TCP up to 30 s
       └─ webbrowser.open() if open=true and the port came up
     └─ usage.record_launch(name)
```

## Key invariants

**A tracked process is identified by `(pid, create_time)`, never by `pid` alone.** `RunningTask.is_alive()` compares both. This is what makes `stop` safe after an abrupt crash, when the OS may have reassigned the PID. Every read path (`state.prune`) drops entries failing this check and rewrites the file, so state self-heals rather than accumulating ghosts.

**No task command ever reaches a shell.** `runner.resolve_argv` splits with `shlex` (Windows rules on Windows) and resolves `argv[0]` through `shutil.which`. Shell metacharacters therefore survive as literal argv entries instead of being interpreted — a repo's `launcher.toml` cannot chain a second command. `shutil.which` is also the mechanism that makes Windows `.cmd` shims work without `shell=True`, so security and portability are satisfied by the same line.

**Teardown covers the whole tree.** Dev servers routinely spawn workers that outlive their parent and keep the port. `_kill_tree` collects `children(recursive=True)`, terminates all, waits 5 s, then kills survivors.

**Restart waits for release.** After teardown, ports linger briefly. `restart_project` polls until each port is actually free (10 s cap) before relaunching, otherwise the new process races its predecessor's socket.

**Discovery does no subprocess work.** Scanning is pure filesystem I/O, which keeps `ls` at ~151 ms for 32 repos. Git status is a subprocess per repo (~383 ms for the same 32), so it's opt-in and executed through a 12-worker thread pool.

## Persistence

Everything lives under `LAUNCHPAD_HOME` (default `%LOCALAPPDATA%\launchpad`):

| File | Contents |
|------|----------|
| `state.json` | Array of running tasks: project, task, pid, create_time, cmd, log path, port |
| `usage.json` | `{project: {count, last_launched}}` for frecency |
| `logs/<project>-<task>.log` | Combined stdout/stderr, header line records the command and cwd |

All reads tolerate corruption: malformed JSON or entries with missing/mistyped keys are skipped rather than crashing, since a truncated file after a hard power loss must not brick the tool.

## Testing strategy

| Layer | Approach |
|-------|----------|
| `config`, `gitinfo`, `logs`, `usage` | Pure functions against temp files and captured git output strings |
| `discovery` | Synthetic repo trees built in `tmp_path` |
| `state` | Uses the live process's real PID and `create_time` so the identity check is exercised for real, not mocked |
| `runner` | Spawns real `python -m http.server` processes, binds real ports, then kills them |
| `commands` / `cli` | Real discovery over temp trees, with `runner`/`preflight` monkeypatched at the seam |
| `tui` | Textual's `run_test()` pilot: real key presses against a real widget tree, asserting focus ownership, filtering, binding dispatch, and timer cleanup |

Tests never call the network, and every test that touches state redirects `LAUNCHPAD_HOME` into `tmp_path` via an autouse fixture, keeping them order-independent.

## Scaffolding (`launch new`)

`scaffold/` wraps each ecosystem's own generator rather than shipping boilerplate:

```
scaffold/
  plan.py         Command | FileSpec | Download steps, Recipe, Plan
  engine.py       name validation, step execution, zip extraction, git/gh
  files.py        glue every stack gets: launcher.toml, .env.example, README, compose
  recipes.py      registry
  recipes_node.py     next  -> create-next-app;  mern -> create-vite + our Express API
  recipes_jvm.py      spring -> start.spring.io starter.zip
  recipes_python.py   fastapi -> uv init --bare
```

The generator writes a `launcher.toml`, so a project is launchable the moment it exists — `launch new x` then `launch up x`. That is the reason scaffolding lives here rather than in a separate tool.

Invariants:

- **Project names are allow-listed, never escaped.** They reach both argv and the filesystem. `validate_name` permits `[a-z0-9][a-z0-9._-]{0,63}`, and rejects `..`, trailing dots, and Windows device names (`con`, `nul`, `com1`…).
- **Downloads are HTTPS-only and extracted defensively.** Every zip entry is resolved and checked against the target directory before extraction, so a crafted archive cannot escape it (zip slip).
- **A failed scaffold leaves the directory in place.** Partial output may contain work; deleting a user's directory is not the tool's call.
- **A generated project must pass its own checks on a clean machine.** FastAPI ships `pytest.ini` so `app` is importable; Spring ships H2 test properties so `mvn test` needs no database. A scaffold whose tests fail on first run is a broken scaffold.

## Deliberate non-goals

- **Not a container orchestrator.** If your stack is compose-shaped, make `docker compose up` a task.
- **Not a version manager.** Use `mise`/`nvm`/`asdf`; launchpad only reports when a binary is missing.
- **No dependency graph.** Sequential start plus port-readiness covers real ordering needs; a DAG would be configuration for its own sake.
