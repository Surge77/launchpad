# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this is

`launchpad` is a terminal project launcher: it discovers git repos under a root directory, infers how to run each one, and manages the resulting processes. Python 3.11, Textual TUI, psutil for process control. Entry point is the `launch` console script → `launchpad.cli:main`.

## Before you change anything

```sh
python -m pytest -q          # know the baseline
python -m pyright            # must stay at 0 errors
```

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first. The module dependency order is strict and downward; don't introduce a cycle.

## Hard constraints

These are not style preferences. Violating them breaks correctness or safety.

1. **Never use `shell=True`, and never build a command string for a shell.** Task commands originate in repo-local `launcher.toml` files. `runner.resolve_argv` splits with `shlex` and resolves `argv[0]` via `shutil.which`, which is also what makes Windows `.cmd` shims work. If you find yourself wanting a shell, you want `shutil.which` instead.
2. **Never identify a process by PID alone.** Always `(pid, create_time)`. See `state.RunningTask.is_alive`. PIDs get recycled; killing a stranger is a real failure mode.
3. **Never kill a process the user didn't ask you to kill.** `free_port` is destructive and reachable only behind an explicit `--force` / `f` key.
4. **Max 300 lines per file.** Split by responsibility.
5. **Keep discovery subprocess-free.** `launch ls` is measured at ~151 ms for 32 repos, and the README states that number. Anything spawning a process per repo must be opt-in and threaded, like `gitinfo`.
6. **Frozen dataclasses stay frozen.** `Project`, `Task`, `Check`, `GitStatus`, `RunningTask`.
7. **Tolerate corrupt state.** All JSON reads skip malformed entries rather than raising. A truncated `state.json` must never brick the tool.

## Testing expectations

- Test file mirrors source path. Behavior-based test names.
- Any test touching state/usage/logs must redirect `LAUNCHPAD_HOME` into `tmp_path` (autouse fixture — copy the existing pattern).
- Tests that spawn processes must stop them in a `finally`.
- Get free ports by binding `127.0.0.1:0`. No network access.
- Don't mock `psutil` in `state`/`runner` tests — the OS behavior *is* the thing under test. Monkeypatch at the `runner`/`preflight` seam for CLI tests.
- 80% coverage minimum. `tui.py` is exempt (Textual wiring, verified manually).

## Where things live

| Task | File |
|------|------|
| Add a subcommand | `cli.py` (parser + dispatch) then `commands.py` (implementation) |
| Support a new language/framework | `discovery.py` — see the checklist in CONTRIBUTING.md |
| Add a pre-launch check | `preflight.py`, return a `Check` with the right level |
| Change spawn/teardown behavior | `runner.py` |
| Change what's persisted | `state.py` (running procs) or `usage.py` (frecency) |
| Change TUI layout/keys | `tui.py` |

## Verification before claiming done

State-changing behavior needs real evidence, not a passing unit test alone:

```sh
launch --root <some-dir> ls
launch --root <some-dir> doctor
launch --root <some-dir> up <project> --no-editor   # then ps, then stop
```

Confirm `launch ps` shows the process, the port actually serves, and after `launch stop` the port is closed. If you changed teardown, verify no orphan holds the port afterwards.

## Documentation to keep in sync

Changing detection rules or config schema means updating **all** of: README table, `docs/CONFIGURATION.md`, and the CONTRIBUTING checklist. Changing the measured numbers (repo count, scan time, test count, coverage) means re-measuring and updating the README — do not let stated numbers drift from reality.
