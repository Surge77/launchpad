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


def test_wait_for_port_open_times_out_quickly() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    assert runner.wait_for_port_open(port, timeout=0.5) is False
