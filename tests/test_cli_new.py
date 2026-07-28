from __future__ import annotations

from pathlib import Path

import pytest

from launchpad import cli, commands, scaffold


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_HOME", str(tmp_path / "home"))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    root.mkdir()
    return root


def test_bare_new_lists_stacks_and_succeeds(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Browsing the catalogue is a legitimate request, not a usage error."""
    exit_code = cli.main(["--root", str(workspace), "new"])

    output = capsys.readouterr().out
    assert exit_code == 0
    for key in ("next", "mern", "spring", "fastapi"):
        assert key in output


def test_name_without_stack_is_a_usage_error(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "new", "thing"])

    assert exit_code == 1
    assert "missing stack" in capsys.readouterr().err


def test_stack_without_name_is_a_usage_error(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "new", "--stack", "fastapi"])

    assert exit_code == 1
    assert "missing project name" in capsys.readouterr().err


def test_unknown_stack_is_reported(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--root", str(workspace), "new", "thing", "--stack", "cobol"])

    assert exit_code == 1
    assert "unknown stack" in capsys.readouterr().err


def test_invalid_name_is_rejected_before_anything_runs(
    workspace: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    executed: list[object] = []
    monkeypatch.setattr(scaffold, "execute", lambda plan, **kwargs: executed.append(plan))

    exit_code = cli.main(["--root", str(workspace), "new", "Bad Name;rm", "--stack", "fastapi"])

    assert exit_code == 1
    assert "invalid project name" in capsys.readouterr().err
    assert executed == [], "nothing may run for a rejected name"
    assert not any(workspace.iterdir())


def test_dry_run_prints_the_plan_without_touching_disk(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["--root", str(workspace), "new", "svc", "--stack", "fastapi", "--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "uv init" in output
    assert "launcher.toml" in output
    assert not (workspace / "svc").exists(), "dry run must not create the project"


def test_dry_run_works_even_when_tools_are_missing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scaffold, "missing_tools", lambda recipe: ["uv"])

    exit_code = cli.main(["--root", str(workspace), "new", "svc", "--stack", "fastapi", "--dry-run"])

    assert exit_code == 0
    assert "uv init" in capsys.readouterr().out


def test_missing_toolchain_blocks_a_real_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scaffold, "missing_tools", lambda recipe: ["uv"])
    executed: list[object] = []
    monkeypatch.setattr(scaffold, "execute", lambda plan, **kwargs: executed.append(plan))

    exit_code = cli.main(["--root", str(workspace), "new", "svc", "--stack", "fastapi"])

    assert exit_code == 1
    assert "needs uv on PATH" in capsys.readouterr().err
    assert executed == []


def test_successful_run_reports_next_commands(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scaffold, "missing_tools", lambda recipe: [])
    monkeypatch.setattr(scaffold, "execute", lambda plan, **kwargs: plan.target)

    exit_code = cli.main(["--root", str(workspace), "new", "svc", "--stack", "fastapi"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "launch up svc" in output


def test_failure_leaves_the_partial_project_and_says_where(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting a user's directory is not ours to decide."""

    def boom(plan: object, **kwargs: object) -> Path:
        raise scaffold.ScaffoldError("uv init failed with exit code 1")

    monkeypatch.setattr(scaffold, "missing_tools", lambda recipe: [])
    monkeypatch.setattr(scaffold, "execute", boom)

    exit_code = cli.main(["--root", str(workspace), "new", "svc", "--stack", "fastapi"])

    error = capsys.readouterr().err
    assert exit_code == 1
    assert "uv init failed" in error
    assert "partial project left at" in error


def test_github_flag_reaches_the_plan(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(scaffold, "missing_tools", lambda recipe: [])
    monkeypatch.setattr(scaffold, "execute", lambda plan, **kwargs: seen.append(plan.github) or plan.target)

    cli.main(["--root", str(workspace), "new", "svc", "--stack", "fastapi", "--github", "private"])

    assert seen == ["private"]


def test_no_git_with_github_is_rejected(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        ["--root", str(workspace), "new", "svc", "--stack", "fastapi", "--no-git", "--github", "public"]
    )

    assert exit_code == 1
    assert "--github requires git" in capsys.readouterr().err


def test_existing_non_empty_target_is_refused(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (workspace / "svc").mkdir()
    (workspace / "svc" / "mine.txt").write_text("keep", encoding="utf-8")

    exit_code = cli.main(["--root", str(workspace), "new", "svc", "--stack", "fastapi"])

    assert exit_code == 1
    assert "already exists and is not empty" in capsys.readouterr().err
    assert (workspace / "svc" / "mine.txt").read_text(encoding="utf-8") == "keep"


def test_scaffolded_project_is_discoverable_by_launch(workspace: Path) -> None:
    """The point of writing launcher.toml: `launch up` works right after `new`."""
    from launchpad.scaffold import engine

    recipe = scaffold.find_recipe("fastapi")
    plan = engine.build_plan(recipe, "svc", workspace, git=False)
    from launchpad.scaffold.plan import FileSpec

    plan = type(plan)(**{**vars(plan), "steps": tuple(s for s in plan.steps if isinstance(s, FileSpec))})
    engine.execute(plan, on_progress=lambda _: None)
    (workspace / "svc" / ".git").mkdir()

    project = commands.find(workspace, "svc")

    assert project is not None
    assert [task.name for task in project.tasks] == ["api"]
    assert project.tasks[0].port == 8000
