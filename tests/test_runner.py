from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from launchpad import runner, state
from launchpad.config import Project, Task


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_HOME", str(tmp_path / "launchpad"))


def python_project(tmp_path: Path, cmd: str, port: int | None = None) -> Project:
    return Project(
        name="demo",
        path=tmp_path,
        stack="Py",
        tasks=(Task(name="run", cmd=cmd, port=port),),
    )


def test_resolve_argv_expands_executable_to_absolute_path() -> None:
    argv = runner.resolve_argv(f"{Path(sys.executable).stem} --version")

    assert Path(argv[0]).is_absolute()
    assert argv[1] == "--version"


def test_resolve_argv_rejects_missing_executable() -> None:
    with pytest.raises(runner.LaunchError, match="command not found"):
        runner.resolve_argv("definitely-not-a-real-binary-xyz")


def test_resolve_argv_rejects_empty_command() -> None:
    with pytest.raises(runner.LaunchError, match="empty command"):
        runner.resolve_argv("   ")


def test_resolve_argv_does_not_interpret_shell_operators() -> None:
    """A repo's launcher.toml must not be able to chain a second command."""
    argv = runner.resolve_argv(f"{Path(sys.executable).stem} -c pass && echo pwned")

    assert "&&" in argv  # passed through as a literal argument, not a shell operator
    assert Path(argv[0]).stem.lower() == Path(sys.executable).stem.lower()


def test_start_task_records_running_process(tmp_path: Path) -> None:
    project = python_project(tmp_path, f"{Path(sys.executable).stem} -c import~time")
    task = Task(name="run", cmd=f'{Path(sys.executable).stem} -c "import time; time.sleep(30)"')

    result = runner.start_task(project, task, wait_for_port=False)

    try:
        tracked = state.for_project("demo")
        assert [t.task for t in tracked] == ["run"]
        assert tracked[0].pid == result.pid
        assert Path(result.log_path).is_file()
    finally:
        runner.stop_project("demo")


def test_start_task_rejects_duplicate_launch(tmp_path: Path) -> None:
    project = python_project(tmp_path, "x")
    task = Task(name="run", cmd=f'{Path(sys.executable).stem} -c "import time; time.sleep(30)"')
    runner.start_task(project, task, wait_for_port=False)

    try:
        with pytest.raises(runner.LaunchError, match="already running"):
            runner.start_task(project, task, wait_for_port=False)
    finally:
        runner.stop_project("demo")


def test_start_task_rejects_missing_cwd(tmp_path: Path) -> None:
    project = Project(name="demo", path=tmp_path, stack="Py", tasks=())
    task = Task(name="run", cmd=f"{Path(sys.executable).stem} -V", subdir="does-not-exist")

    with pytest.raises(runner.LaunchError, match="cwd does not exist"):
        runner.start_task(project, task, wait_for_port=False)


def test_stop_project_terminates_and_clears_state(tmp_path: Path) -> None:
    project = python_project(tmp_path, "x")
    task = Task(name="run", cmd=f'{Path(sys.executable).stem} -c "import time; time.sleep(30)"')
    result = runner.start_task(project, task, wait_for_port=False)

    stopped = runner.stop_project("demo")

    assert stopped == ["run"]
    assert state.load() == []
    assert not any(t.pid == result.pid and t.is_alive() for t in state.load())


def test_stop_project_is_safe_when_nothing_running() -> None:
    assert runner.stop_project("nothing-here") == []


def test_start_project_without_tasks_raises(tmp_path: Path) -> None:
    project = Project(name="empty", path=tmp_path, stack="-", tasks=())

    with pytest.raises(runner.LaunchError, match="no tasks"):
        runner.start_project(project)


def test_is_port_open_detects_listening_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        assert runner.is_port_open(port) is True


def test_is_port_open_false_for_closed_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    assert runner.is_port_open(port) is False


def sleeper(name: str = "run") -> Task:
    return Task(name=name, cmd=f'{Path(sys.executable).stem} -c "import time; time.sleep(30)"')


def http_server_task(port: int, name: str = "web") -> Task:
    return Task(name=name, cmd=f"{Path(sys.executable).stem} -m http.server {port}", port=port)


def test_restart_project_replaces_the_process(tmp_path: Path) -> None:
    project = Project(name="demo", path=tmp_path, stack="Py", tasks=(sleeper(),))
    first = runner.start_project(project, wait_for_ports=False)[0]

    try:
        second = runner.restart_project(project, wait_for_ports=False)[0]

        assert second.pid != first.pid
        assert [task.pid for task in state.for_project("demo")] == [second.pid]
    finally:
        runner.stop_project("demo")


def test_restart_works_when_nothing_was_running(tmp_path: Path) -> None:
    project = Project(name="demo", path=tmp_path, stack="Py", tasks=(sleeper(),))

    try:
        results = runner.restart_project(project, wait_for_ports=False)

        assert len(results) == 1
    finally:
        runner.stop_project("demo")


def test_restart_frees_its_own_port_before_relaunch(tmp_path: Path) -> None:
    """The relaunch must not hit EADDRINUSE against the process it just killed."""
    port = _free_port_number()
    project = Project(name="demo", path=tmp_path, stack="Py", tasks=(http_server_task(port),))
    first = runner.start_project(project)
    _require_serving(first[0])

    try:
        result = runner.restart_project(project)

        assert result[0].port_ready is True, _log_dump(result[0])
    finally:
        runner.stop_project("demo")


def test_start_project_records_usage(tmp_path: Path) -> None:
    from launchpad import usage

    project = Project(name="demo", path=tmp_path, stack="Py", tasks=(sleeper(),))

    try:
        runner.start_project(project, wait_for_ports=False)

        assert usage.load()["demo"].count == 1
    finally:
        runner.stop_project("demo")


def test_free_port_returns_none_when_port_is_idle() -> None:
    assert runner.free_port(_free_port_number()) is None


def test_free_port_kills_the_listening_process(tmp_path: Path) -> None:
    port = _free_port_number()
    project = Project(name="squatter", path=tmp_path, stack="Py", tasks=(http_server_task(port),))
    started = runner.start_project(project)
    _require_serving(started[0])

    owner = runner.free_port(port)

    try:
        assert owner is not None, "free_port must report which process it killed"
        assert runner.is_port_open(port) is False
    finally:
        runner.stop_project("squatter")


def _log_dump(result: runner.LaunchResult) -> str:
    """Failure message carrying the child's own output — otherwise a CI failure
    on another OS says only 'False is not True'."""
    try:
        body = Path(result.log_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        body = f"<log unreadable: {exc}>"
    return f"task {result.task} port {result.port} not ready.\n--- log ---\n{body}"


def _require_serving(result: runner.LaunchResult) -> None:
    if not result.port_ready:
        pytest.fail(_log_dump(result))


def test_wait_for_port_release_returns_true_for_idle_port() -> None:
    assert runner._wait_for_port_release(_free_port_number(), timeout=1.0) is True


def test_wait_for_port_release_times_out_while_port_is_held() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        # Backlog must exceed the number of probes: each poll opens a connection
        # the test server never accepts, and a full backlog would refuse the
        # next probe, making the port look released when it is not.
        server.listen(128)
        port = server.getsockname()[1]

        assert runner._wait_for_port_release(port, timeout=0.6) is False


def test_open_editor_raises_when_editor_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_EDITOR", "definitely-not-an-editor-xyz")
    project = Project(name="demo", path=tmp_path, stack="Py", tasks=())

    with pytest.raises(runner.LaunchError, match="editor not found"):
        runner.open_editor(project)


def _free_port_number() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_child_environment_is_unbuffered(tmp_path: Path) -> None:
    """Dev server output must reach the log immediately, not when a buffer fills."""
    project = Project(name="demo", path=tmp_path, stack="Py", tasks=())
    task = Task(
        name="print",
        cmd=f'{Path(sys.executable).stem} -c "import time; print(\'hello\'); time.sleep(5)"',
    )
    result = runner.start_task(project, task, wait_for_port=False)

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if "hello" in Path(result.log_path).read_text(encoding="utf-8", errors="replace"):
                return
            time.sleep(0.1)
        pytest.fail(f"output never flushed:\n{_log_dump(result)}")
    finally:
        runner.stop_project("demo")


def test_task_env_overrides_the_unbuffered_default(tmp_path: Path) -> None:
    project = Project(name="demo", path=tmp_path, stack="Py", tasks=())
    script = f'{Path(sys.executable).stem} -c "import os; print(os.environ[\'PYTHONUNBUFFERED\'])"'
    task = Task(name="env", cmd=script, env={"PYTHONUNBUFFERED": "0"})
    result = runner.start_task(project, task, wait_for_port=False)

    try:
        time.sleep(1.5)
        assert "0" in Path(result.log_path).read_text(encoding="utf-8", errors="replace")
    finally:
        runner.stop_project("demo")


def test_wait_for_port_open_times_out_quickly() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    assert runner.wait_for_port_open(port, timeout=0.5) is False
