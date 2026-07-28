from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Log

from launchpad.tui import LaunchpadApp


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_HOME", str(tmp_path / "home"))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    for name, port in (("alpha-web", 3000), ("beta-api", 8000)):
        repo = root / name
        (repo / ".git").mkdir(parents=True)
        (repo / "package.json").write_text(
            json.dumps({"scripts": {"dev": "next dev"}, "dependencies": {"next": "16"}}), encoding="utf-8"
        )
        (repo / "tsconfig.json").write_text("{}", encoding="utf-8")
    return root


def test_app_attributes_do_not_shadow_textual_internals() -> None:
    """`visible` and `_timers` already exist on Textual base classes; assigning
    to either breaks the app at construction or first mount."""
    app = LaunchpadApp(Path.cwd())
    reserved = set(dir(App))

    collisions = {
        name
        for name in vars(app)
        if name in reserved and not callable(getattr(App, name, None))
    }

    assert collisions == set(), f"attributes shadow Textual internals: {collisions}"


async def test_mounts_and_lists_projects(workspace: Path) -> None:
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#projects", DataTable)

        assert table.row_count == 2
        assert [str(cell) for cell in table.get_row_at(0)][1] in {"alpha-web", "beta-api"}


async def test_table_owns_the_keyboard_at_startup(workspace: Path) -> None:
    """The hidden filter Input must not swallow bindings on launch."""
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()

        assert isinstance(app.focused, DataTable)
        assert app.query_one("#filter", Input).can_focus is False


async def test_slash_opens_the_filter_instead_of_typing_a_slash(workspace: Path) -> None:
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()

        field = app.query_one("#filter", Input)
        assert isinstance(app.focused, Input)
        assert field.value == "", "the slash keystroke must not land in the input"


async def test_filter_narrows_the_list(workspace: Path) -> None:
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for char in "beta":
            await pilot.press(char)
        await pilot.pause()

        table = app.query_one("#projects", DataTable)
        assert table.row_count == 1
        assert [str(cell) for cell in table.get_row_at(0)][1] == "beta-api"


async def test_escape_clears_the_filter_and_returns_focus(workspace: Path) -> None:
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for char in "beta":
            await pilot.press(char)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.query_one("#filter", Input).value == ""
        assert app.query_one("#projects", DataTable).row_count == 2
        assert isinstance(app.focused, DataTable)


async def test_doctor_binding_reports_on_the_selected_project(workspace: Path) -> None:
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()

        written = "\n".join(str(line) for line in app.query_one("#output", Log).lines)
        assert "⌕" in written
        assert "dev" in written, "expected the detected task to be checked"


async def test_rescan_binding_reports_project_count(workspace: Path) -> None:
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        written = "\n".join(str(line) for line in app.query_one("#output", Log).lines)
        assert "2 projects" in written


async def test_stop_binding_is_safe_when_nothing_is_running(workspace: Path) -> None:
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()

        written = "\n".join(str(line) for line in app.query_one("#output", Log).lines)
        assert "stopped nothing" in written


async def test_empty_workspace_does_not_crash_bindings(tmp_path: Path) -> None:
    """No rows means no selection; every binding must no-op quietly."""
    empty = tmp_path / "empty"
    empty.mkdir()
    app = LaunchpadApp(empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        for key in ("d", "s", "e", "R", "f", "enter"):
            await pilot.press(key)
        await pilot.pause()

        assert app.query_one("#projects", DataTable).row_count == 0


async def test_poll_timers_stop_on_unmount(workspace: Path) -> None:
    """A timer that outlives the DOM queries missing widgets and crashes on quit."""
    app = LaunchpadApp(workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app._poll_timers) == 2

    assert app._poll_timers == []
