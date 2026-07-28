from __future__ import annotations

import json
from pathlib import Path

import pytest

from launchpad import cli, commands, gitinfo, preflight, runner, state
from launchpad.config import Project, Task


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_HOME", str(tmp_path / "launchpad-home"))


@pytest.fixture(autouse=True)
def passing_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to a clean pre-flight; tests that care override it explicitly."""
    monkeypatch.setattr(preflight, "check_project", lambda project, check_ports=True: [])


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    repo = root / "collabboard"
    (repo / ".git").mkdir(parents=True)
    (repo / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev"}}), encoding="utf-8")
    (repo / "tsconfig.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def no_op_launch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    launched: list[str] = []
    monkeypatch.setattr(runner, "open_editor", lambda project: None)
    monkeypatch.setattr(
        runner,
        "start_project",
        lambda project, wait_for_ports=True: launched.append(project.name)
        or [runner.LaunchResult(task="dev", pid=4242, port=3000, url=None, log_path=Path("d.log"), port_ready=True)],
    )
    return launched


def test_ls_lists_projects_and_stack(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "ls"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "collabboard" in output
    assert "TS" in output
    assert "1 projects" in output


def test_ls_json_emits_machine_readable_payload(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--root", str(workspace), "ls", "--json"])

    payload = json.loads(capsys.readouterr().out)

    assert payload[0]["name"] == "collabboard"
    assert payload[0]["running"] is False
    assert payload[0]["tasks"][0]["port"] == 3000


def test_ls_git_flag_includes_branch(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        gitinfo, "statuses", lambda paths: {name: gitinfo.GitStatus(branch="develop", dirty=2) for name in paths}
    )

    cli.main(["--root", str(workspace), "ls", "--git"])

    assert "develop *2" in capsys.readouterr().out


def test_missing_root_returns_exit_code_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(tmp_path / "nope"), "ls"])

    assert exit_code == 2
    assert "root does not exist" in capsys.readouterr().err


def test_up_unknown_project_returns_error(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "up", "does-not-exist"])

    assert exit_code == 1
    assert "no project named" in capsys.readouterr().err


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


def test_up_no_editor_flag_skips_editor(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(runner, "open_editor", lambda project: opened.append(project.name))
    monkeypatch.setattr(runner, "start_project", lambda project, wait_for_ports=True: [])

    cli.main(["--root", str(workspace), "up", "collabboard", "--no-editor"])

    assert opened == []


def test_up_matches_project_by_partial_name(workspace: Path, no_op_launch: list[str]) -> None:
    assert cli.main(["--root", str(workspace), "up", "collab", "--no-editor"]) == 0
    assert no_op_launch == ["collabboard"]


def test_up_accepts_several_projects(tmp_path: Path, no_op_launch: list[str]) -> None:
    root = tmp_path / "repos"
    for name in ("api", "web"):
        (root / name / ".git").mkdir(parents=True)
        (root / name / "launcher.toml").write_text('[[task]]\nname="run"\ncmd="x"\n', encoding="utf-8")

    assert cli.main(["--root", str(root), "up", "api", "web", "--no-editor"]) == 0
    assert sorted(no_op_launch) == ["api", "web"]


def test_up_blocks_on_preflight_error(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        preflight,
        "check_project",
        lambda project, check_ports=True: [preflight.Check("error", "port 3000 held by pid 9 (node.exe)")],
    )
    started: list[str] = []
    monkeypatch.setattr(runner, "start_project", lambda project, wait_for_ports=True: started.append("no") or [])

    exit_code = cli.main(["--root", str(workspace), "up", "collabboard"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "pre-flight failed" in captured.err
    assert "node.exe" in captured.out
    assert started == [], "must not launch when pre-flight fails"


def test_force_frees_the_port_then_launches(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_op_launch: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        preflight, "check_project", lambda project, check_ports=True: [preflight.Check("error", "port 3000 held")]
    )
    monkeypatch.setattr(runner, "free_port", lambda port: preflight.PortOwner(pid=9, name="node.exe"))

    exit_code = cli.main(["--root", str(workspace), "up", "collabboard", "--no-editor", "--force"])

    assert exit_code == 0
    assert "freed port 3000" in capsys.readouterr().out
    assert no_op_launch == ["collabboard"]


def test_up_warns_but_proceeds_on_warning_level_check(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_op_launch: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        preflight,
        "check_project",
        lambda project, check_ports=True: [preflight.Check("warn", ".env.example exists but .env is missing")],
    )

    exit_code = cli.main(["--root", str(workspace), "up", "collabboard", "--no-editor"])

    assert exit_code == 0
    assert ".env is missing" in capsys.readouterr().out
    assert no_op_launch == ["collabboard"]


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


def test_restart_calls_restart_project(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    restarted: list[str] = []
    monkeypatch.setattr(
        runner, "restart_project", lambda project, wait_for_ports=True: restarted.append(project.name) or []
    )

    assert cli.main(["--root", str(workspace), "restart", "collabboard"]) == 0
    assert restarted == ["collabboard"]


def test_restart_skips_port_conflict_checks(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restart owns the port it is about to reclaim, so it must not self-block."""
    seen: list[bool] = []
    monkeypatch.setattr(
        preflight,
        "check_project",
        lambda project, check_ports=True: seen.append(check_ports) or [],
    )
    monkeypatch.setattr(runner, "restart_project", lambda project, wait_for_ports=True: [])

    cli.main(["--root", str(workspace), "restart", "collabboard"])

    assert seen == [False]


def test_restart_unknown_project_errors(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(workspace), "restart", "ghost"]) == 1
    assert "no project named" in capsys.readouterr().err


def test_doctor_reports_clean_project(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "doctor", "collabboard"])

    assert exit_code == 0
    assert "no blocking issues" in capsys.readouterr().out


def test_doctor_returns_nonzero_when_blocked(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        preflight, "check_project", lambda project, check_ports=True: [preflight.Check("error", "'pnpm' not on PATH")]
    )

    exit_code = cli.main(["--root", str(workspace), "doctor"])

    captured = capsys.readouterr().out
    assert exit_code == 1
    assert "not on PATH" in captured
    assert "1 blocking issue" in captured


def test_workspace_audit_skips_projects_with_nothing_to_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A library with no dev server is not a broken project."""
    monkeypatch.undo()  # use the real preflight for this one
    root = tmp_path / "repos"
    (root / "some-library" / ".git").mkdir(parents=True)
    (root / "some-library" / "pyproject.toml").write_text("[project]\nname='lib'\n", encoding="utf-8")

    exit_code = cli.main(["--root", str(root), "doctor"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "no blocking issues" in output
    assert "some-library" not in output


def test_doctor_by_name_still_explains_a_project_with_no_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.undo()
    root = tmp_path / "repos"
    (root / "some-library" / ".git").mkdir(parents=True)

    exit_code = cli.main(["--root", str(root), "doctor", "some-library"])

    assert exit_code == 1
    assert "launcher.toml" in capsys.readouterr().out


def test_doctor_unknown_project_errors(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(workspace), "doctor", "ghost"]) == 1
    assert "no project named" in capsys.readouterr().err


def test_ps_reports_nothing_running(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(workspace), "ps"]) == 0
    assert "nothing running" in capsys.readouterr().out


def test_ps_json_is_empty_list_when_idle(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--root", str(workspace), "ps", "--json"])

    assert json.loads(capsys.readouterr().out) == []


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


def test_stop_named_project(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "stop_project", lambda name: ["dev", "db"])

    exit_code = cli.main(["--root", str(workspace), "stop", "collabboard"])

    assert exit_code == 0
    assert "dev, db" in capsys.readouterr().out


def test_stop_without_name_reports_nothing_when_idle(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(workspace), "stop"]) == 0
    assert "nothing running" in capsys.readouterr().out


def test_stop_without_name_stops_every_tracked_project(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracked = [
        state.RunningTask(project="a", task="dev", pid=1, create_time=1.0, cmd="x", log_path="a.log"),
        state.RunningTask(project="b", task="dev", pid=2, create_time=1.0, cmd="y", log_path="b.log"),
    ]
    monkeypatch.setattr(state, "prune", lambda: tracked)
    stopped: list[str] = []
    monkeypatch.setattr(runner, "stop_project", lambda name: stopped.append(name) or [name])

    cli.main(["--root", str(workspace), "stop"])

    assert sorted(stopped) == ["a", "b"]


def test_logs_prints_history(workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log_file = tmp_path / "collabboard-dev.log"
    log_file.write_text("compiled successfully\n", encoding="utf-8")
    state.save(
        [
            state.RunningTask(
                project="collabboard",
                task="dev",
                pid=1,
                create_time=1.0,
                cmd="pnpm dev",
                log_path=str(log_file),
            )
        ]
    )

    exit_code = cli.main(["--root", str(workspace), "logs", "collabboard"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "compiled successfully" in output


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


def test_find_returns_project_with_detected_task(workspace: Path) -> None:
    project = commands.find(workspace, "collabboard")

    assert project is not None
    assert project.tasks == (Task(name="dev", cmd="npm run dev", port=3000, open_browser=True),)
