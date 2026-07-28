from __future__ import annotations

import json
from pathlib import Path

import pytest

from launchpad import cli, runner, state
from launchpad.config import Project, Task


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_HOME", str(tmp_path / "launchpad-home"))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    repo = root / "collabboard"
    (repo / ".git").mkdir(parents=True)
    (repo / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev"}}), encoding="utf-8")
    (repo / "tsconfig.json").write_text("{}", encoding="utf-8")
    return root


def test_ls_lists_projects_and_stack(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "ls"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "collabboard" in output
    assert "TS" in output
    assert "1 projects" in output


def test_missing_root_returns_exit_code_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(tmp_path / "nope"), "ls"])

    assert exit_code == 2
    assert "root does not exist" in capsys.readouterr().err


def test_up_unknown_project_returns_error(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "up", "does-not-exist"])

    assert exit_code == 1
    assert "no project named" in capsys.readouterr().err


def test_up_project_without_tasks_explains_how_to_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repos"
    (root / "docs" / ".git").mkdir(parents=True)

    exit_code = cli.main(["--root", str(root), "up", "docs"])

    assert exit_code == 1
    assert "launcher.toml" in capsys.readouterr().err


def test_up_starts_tasks_and_opens_editor(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(runner, "open_editor", lambda project: opened.append(project.name))
    monkeypatch.setattr(
        runner,
        "start_project",
        lambda project, wait_for_ports=True: [
            runner.LaunchResult(task="dev", pid=4242, port=3000, url=None, log_path=Path("dev.log"), port_ready=True)
        ],
    )

    exit_code = cli.main(["--root", str(workspace), "up", "collabboard"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert opened == ["collabboard"]
    assert "pid 4242" in output
    assert ":3000" in output


def test_up_no_editor_flag_skips_editor(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(runner, "open_editor", lambda project: opened.append(project.name))
    monkeypatch.setattr(runner, "start_project", lambda project, wait_for_ports=True: [])

    cli.main(["--root", str(workspace), "up", "collabboard", "--no-editor"])

    assert opened == []


def test_up_matches_project_by_partial_name(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(runner, "open_editor", lambda project: None)
    monkeypatch.setattr(
        runner,
        "start_project",
        lambda project, wait_for_ports=True: started.append(project.name) or [],
    )

    assert cli.main(["--root", str(workspace), "up", "collab", "--no-editor"]) == 0
    assert started == ["collabboard"]


def test_up_reports_launch_failure(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(project: Project, wait_for_ports: bool = True) -> list[runner.LaunchResult]:
        raise runner.LaunchError("port 3000 already bound")

    monkeypatch.setattr(runner, "open_editor", lambda project: None)
    monkeypatch.setattr(runner, "start_project", boom)

    exit_code = cli.main(["--root", str(workspace), "up", "collabboard"])

    assert exit_code == 1
    assert "port 3000 already bound" in capsys.readouterr().err


def test_ps_reports_nothing_running(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(workspace), "ps"]) == 0
    assert "nothing running" in capsys.readouterr().out


def test_ps_lists_tracked_tasks(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task = state.RunningTask(
        project="collabboard",
        task="dev",
        pid=999_999,
        create_time=1.0,
        cmd="pnpm dev",
        log_path="dev.log",
        port=3000,
    )
    monkeypatch.setattr(state, "prune", lambda: [task])

    cli.main(["--root", str(workspace), "ps"])

    output = capsys.readouterr().out
    assert "collabboard/dev" in output
    assert "pnpm dev" in output


def test_stop_named_project(workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(runner, "stop_project", lambda name: ["dev", "db"])

    exit_code = cli.main(["--root", str(workspace), "stop", "collabboard"])

    assert exit_code == 0
    assert "dev, db" in capsys.readouterr().out


def test_stop_without_name_reports_nothing_when_idle(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(workspace), "stop"]) == 0
    assert "nothing running" in capsys.readouterr().out


def test_stop_without_name_stops_every_tracked_project(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked = [
        state.RunningTask(project="a", task="dev", pid=1, create_time=1.0, cmd="x", log_path="a.log"),
        state.RunningTask(project="b", task="dev", pid=2, create_time=1.0, cmd="y", log_path="b.log"),
    ]
    monkeypatch.setattr(state, "prune", lambda: tracked)
    stopped: list[str] = []
    monkeypatch.setattr(runner, "stop_project", lambda name: stopped.append(name) or [name])

    cli.main(["--root", str(workspace), "stop"])

    assert sorted(stopped) == ["a", "b"]


def test_logs_prints_recorded_paths(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state.save(
        [
            state.RunningTask(
                project="collabboard",
                task="dev",
                pid=1,
                create_time=1.0,
                cmd="pnpm dev",
                log_path="C:/logs/collabboard-dev.log",
            )
        ]
    )

    exit_code = cli.main(["--root", str(workspace), "logs", "collabboard"])

    assert exit_code == 0
    assert "collabboard-dev.log" in capsys.readouterr().out


def test_logs_missing_project_returns_error(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "logs", "ghost"])

    assert exit_code == 1
    assert "no logs recorded" in capsys.readouterr().err


def test_root_can_come_from_environment(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LAUNCHPAD_ROOT", str(workspace))

    assert cli.main(["ls"]) == 0
    assert "collabboard" in capsys.readouterr().out


def test_no_subcommand_starts_the_tui(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[Path] = []
    import launchpad.tui as tui_module

    monkeypatch.setattr(tui_module, "run_tui", lambda root: launched.append(root))

    assert cli.main(["--root", str(workspace)]) == 0
    assert launched == [workspace.resolve()]


def test_detected_task_survives_round_trip_through_discovery(workspace: Path) -> None:
    project = cli._find(workspace, "collabboard")

    assert project is not None
    assert project.tasks == (Task(name="dev", cmd="npm run dev", port=3000, open_browser=True),)
