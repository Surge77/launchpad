# Contributing

## Setup

```sh
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # macOS / Linux
pip install -e ".[dev]"
```

## The gates

Both must pass before anything merges. CI runs them on Linux, macOS, and Windows.

```sh
python -m pytest --cov=launchpad --cov-report=term-missing
python -m pyright
```

- **Tests:** all green, no skips without a linked issue.
- **Coverage:** 80% minimum overall. New non-UI modules should land at 85%+.
- **Types:** zero pyright errors. A change that imports cleanly but fails pyright is not done.

## House rules

- **Max 300 lines per file.** Split by responsibility before adding more.
- **No `shell=True`.** Ever. Task commands come from user config files; see [ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Comment *why*, not *what*.** The non-obvious constraint, the bug being worked around, the invariant being upheld.
- **No new dependencies** without a strong case. The runtime is Python 3.11 + Textual + psutil; `tomllib` is stdlib. Anything else needs a reason the stdlib can't cover.
- **Type hints on every public signature**, params and return.
- **Immutability:** `Project`, `Task`, `Check`, `GitStatus`, `RunningTask` are frozen dataclasses. Keep them that way.

## Tests

Mirror the source path: `src/launchpad/runner.py` → `tests/test_runner.py`.

Name tests for behavior, not implementation:

```python
def test_restart_frees_its_own_port_before_relaunch() -> None: ...   # good
def test_restart_project_2() -> None: ...                            # no
```

Rules that matter here specifically:

- **Redirect state.** Any test touching state, usage, or logs must set `LAUNCHPAD_HOME` into `tmp_path` — there's an autouse fixture in the affected test modules; follow the pattern.
- **Clean up processes.** Tests that spawn real processes must `runner.stop_project(...)` in a `finally`. A leaked `http.server` will break later runs by holding a port.
- **No network.** Bind `127.0.0.1` with port `0` to get a free port; never reach outside the machine.
- **Prefer real over mocked at the OS boundary.** The PID-identity and port logic is the heart of this tool; mocking `psutil` there would test the mock. Monkeypatch at the `runner`/`preflight` seam instead when testing the CLI.

## Adding stack detection

To support a new ecosystem, edit `discovery.py`:

1. Add the marker file to `detect_stack`.
2. Add a `_<lang>_tasks(path)` function returning a `tuple[Task, ...]`.
3. Register it in the `detectors` map in `detect_tasks`.
4. Add tests: stack label, generated command, port, and the no-runnable-target case.
5. Document the rule in the table in [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and the README.

Keep detection conservative. Guessing a wrong command is worse than returning nothing — a `—` in the list plus `launch doctor` telling the user to write four lines of `launcher.toml` is an honest outcome.

## Commits and PRs

Conventional commits, one logical change each:

```
feat(preflight): name the process holding a conflicting port
fix(gitinfo): parse "No commits yet on main" as a plain branch
docs(config): document env table validation
test(runner): cover restart port-release path
```

PRs should state what changed, why, and how you verified it. Include command output for behavior changes — a claim that something works is not evidence that it does.

## Reporting bugs

Include your OS, Python version, the project layout that triggers it (`launch ls --json` output is ideal), and `launch doctor <name>`. For launch failures, the task log at `%LOCALAPPDATA%\launchpad\logs\<project>-<task>.log` usually contains the actual error.
