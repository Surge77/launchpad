from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from launchpad import preflight
from launchpad.config import Project, Task

PYTHON = Path(sys.executable).stem


def project(tmp_path: Path, *tasks: Task) -> Project:
    return Project(name="demo", path=tmp_path, stack="Py", tasks=tasks)


def test_project_without_tasks_is_a_blocking_error(tmp_path: Path) -> None:
    checks = preflight.check_project(project(tmp_path))

    assert preflight.blocking(checks)
    assert "launcher.toml" in checks[0].message


def test_missing_executable_is_blocking(tmp_path: Path) -> None:
    checks = preflight.check_project(project(tmp_path, Task(name="web", cmd="totally-not-a-binary-xyz serve")))

    blockers = preflight.blocking(checks)
    assert blockers
    assert "not on PATH" in blockers[0].message


def test_present_executable_passes(tmp_path: Path) -> None:
    checks = preflight.check_project(project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V")))

    assert not preflight.blocking(checks)


def test_missing_cwd_is_blocking(tmp_path: Path) -> None:
    checks = preflight.check_project(project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V", subdir="nope")))

    assert any("cwd missing" in check.message for check in preflight.blocking(checks))


def test_env_example_without_env_warns(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("API_KEY=", encoding="utf-8")

    checks = preflight.check_project(project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V")))

    warnings = [check for check in checks if check.level == "warn"]
    assert warnings
    assert ".env is missing" in warnings[0].message


def test_env_example_with_env_does_not_warn(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("API_KEY=", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=1", encoding="utf-8")

    checks = preflight.check_project(project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V")))

    assert not [check for check in checks if check.level == "warn"]


def test_no_env_example_produces_no_env_check(tmp_path: Path) -> None:
    checks = preflight.check_project(project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V")))

    assert not any(".env" in check.message for check in checks)


def test_occupied_port_is_blocking_and_names_the_owner(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        checks = preflight.check_project(project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V", port=port)))

        blockers = preflight.blocking(checks)
        assert blockers, "a bound port must block launch"
        assert f"port {port} already held" in blockers[0].message


def test_check_ports_false_ignores_an_occupied_port(tmp_path: Path) -> None:
    """Restart replaces the process holding the port, so it is not a conflict."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        checks = preflight.check_project(
            project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V", port=port)), check_ports=False
        )

        assert not preflight.blocking(checks)
        assert not any("port" in check.message for check in checks)


def test_check_ports_false_still_validates_the_executable(tmp_path: Path) -> None:
    checks = preflight.check_project(
        project(tmp_path, Task(name="web", cmd="no-such-binary-xyz", port=3000)), check_ports=False
    )

    assert any("not on PATH" in check.message for check in preflight.blocking(checks))


def test_free_port_passes(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    checks = preflight.check_project(project(tmp_path, Task(name="web", cmd=f"{PYTHON} -V", port=port)))

    assert not preflight.blocking(checks)


def test_port_owner_returns_none_for_free_port() -> None:
    assert preflight.port_owner(1, host_ports={}) is None


def test_port_owner_reads_supplied_map() -> None:
    owner = preflight.PortOwner(pid=42, name="node.exe")

    assert preflight.port_owner(3000, host_ports={3000: owner}) is owner


def test_listening_ports_finds_a_bound_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        assert port in preflight.listening_ports()


def test_port_owner_renders_pid_and_name() -> None:
    assert preflight.PortOwner(pid=7, name="node.exe").render() == "pid 7 (node.exe)"


@pytest.mark.parametrize(("level", "mark"), [("ok", "✓"), ("warn", "!"), ("error", "✗")])
def test_check_renders_severity_mark(level: str, mark: str) -> None:
    check = preflight.Check(level, "message")  # type: ignore[arg-type]

    assert check.render().startswith(mark)


def test_blocking_filters_out_warnings_and_ok() -> None:
    checks = [
        preflight.Check("ok", "fine"),
        preflight.Check("warn", "hmm"),
        preflight.Check("error", "bad"),
    ]

    assert [check.message for check in preflight.blocking(checks)] == ["bad"]
