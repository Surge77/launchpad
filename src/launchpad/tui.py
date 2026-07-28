"""Textual UI: pick a project, launch its environment, watch status."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Log

from launchpad import runner, state
from launchpad.config import Project
from launchpad.discovery import discover

COLUMNS = ("", "project", "stack", "tasks", "status")
RUNNING_MARK = "●"
IDLE_MARK = "○"


class LaunchpadApp(App[None]):
    CSS = """
    Input { display: none; }
    Input.visible { display: block; }
    Log { height: 8; border-top: solid $accent; }
    """

    BINDINGS = [
        Binding("enter", "launch", "launch"),
        Binding("e", "editor", "editor"),
        Binding("s", "stop", "stop"),
        Binding("slash", "filter", "filter"),
        Binding("r", "rescan", "rescan"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.projects: list[Project] = []
        self.visible: list[Project] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Input(placeholder="filter…", id="filter")
            yield DataTable(id="projects", cursor_type="row")
            yield Log(id="output", highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "launchpad"
        table = self.query_one("#projects", DataTable)
        table.add_columns(*COLUMNS)
        self.action_rescan()

    # -- actions ---------------------------------------------------------

    def action_rescan(self) -> None:
        self.projects = discover(self.root)
        self._apply_filter(self.query_one("#filter", Input).value)
        self._log(f"scanned {self.root} — {len(self.projects)} projects")

    def action_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.add_class("visible")
        field.focus()

    def action_launch(self) -> None:
        project = self._selected()
        if project is None:
            return
        self._log(f"→ {project.name}")
        try:
            runner.open_editor(project)
            self._log(f"  ✓ editor {project.path}")
            for result in runner.start_project(project):
                port = f":{result.port}" if result.port else ""
                ready = "" if result.port is None else (" ready" if result.port_ready else " (port not up yet)")
                self._log(f"  ✓ {result.task} pid {result.pid} {port}{ready}")
        except runner.LaunchError as exc:
            self._log(f"  ✗ {exc}")
        self._refresh_rows()

    def action_editor(self) -> None:
        project = self._selected()
        if project is None:
            return
        try:
            runner.open_editor(project)
            self._log(f"✓ editor {project.name}")
        except runner.LaunchError as exc:
            self._log(f"✗ {exc}")

    def action_stop(self) -> None:
        project = self._selected()
        if project is None:
            return
        stopped = runner.stop_project(project.name)
        self._log(f"■ {project.name}: stopped {', '.join(stopped) if stopped else 'nothing'}")
        self._refresh_rows()

    # -- events ----------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.remove_class("visible")
        self.query_one("#projects", DataTable).focus()

    # -- helpers ---------------------------------------------------------

    def _apply_filter(self, needle: str) -> None:
        lowered = needle.strip().lower()
        self.visible = [p for p in self.projects if lowered in p.name.lower()] if lowered else list(self.projects)
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        table = self.query_one("#projects", DataTable)
        cursor = table.cursor_row
        table.clear()
        running = {task.project: task for task in state.prune()}
        for project in self.visible:
            active = project.name in running
            status = f"{RUNNING_MARK} running" if active else f"{IDLE_MARK} —"
            table.add_row(
                RUNNING_MARK if active else "",
                project.name,
                project.stack,
                project.summary,
                status,
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def _selected(self) -> Project | None:
        table = self.query_one("#projects", DataTable)
        if not self.visible or table.cursor_row < 0 or table.cursor_row >= len(self.visible):
            return None
        return self.visible[table.cursor_row]

    def _log(self, message: str) -> None:
        self.query_one("#output", Log).write_line(message)


def run_tui(root: Path) -> None:
    LaunchpadApp(root).run()
