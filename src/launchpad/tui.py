"""Textual UI: browse projects, launch environments, watch output live."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header, Input, Log

from launchpad import gitinfo, logs, preflight, runner, state, usage
from launchpad.config import Project
from launchpad.discovery import discover

COLUMNS = ("", "project", "stack", "tasks", "branch")
RUNNING_MARK = "●"
IDLE_MARK = "○"
LOG_POLL_SECONDS = 0.4
GIT_REFRESH_SECONDS = 10.0


class LaunchpadApp(App[None]):
    CSS = """
    Input { display: none; }
    Input.visible { display: block; }
    Log { height: 12; border-top: solid $accent; }
    """

    BINDINGS = [
        Binding("enter", "launch", "launch"),
        Binding("e", "editor", "editor"),
        Binding("s", "stop", "stop"),
        Binding("R", "restart", "restart"),
        Binding("d", "doctor", "doctor"),
        Binding("f", "force", "force"),
        Binding("slash", "filter", "filter"),
        Binding("escape", "close_filter", "clear filter", show=False),
        Binding("r", "rescan", "rescan"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.projects: list[Project] = []
        # Attribute names here must not shadow Textual's own: `visible` is a CSS
        # property on DOMNode and `_timers` is a set on MessagePump, and either
        # collision breaks the app at construction or first mount. test_tui.py
        # asserts this class adds no colliding names.
        self.matching: list[Project] = []
        self.git: dict[str, gitinfo.GitStatus] = {}
        self._followers: dict[str, logs.LogFollower] = {}
        self._followed_project: str | None = None
        self._poll_timers: list[Timer] = []

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
        # A `display: none` widget still accepts focus in Textual, and the Input
        # is first in the DOM — so without this the hidden filter box owned the
        # keyboard at startup and every binding (enter/e/s/d) was swallowed.
        self.query_one("#filter", Input).can_focus = False
        table.focus()
        self.action_rescan()
        self._poll_timers = [
            self.set_interval(LOG_POLL_SECONDS, self._pump_logs),
            self.set_interval(GIT_REFRESH_SECONDS, self._refresh_git),
        ]

    def on_unmount(self) -> None:
        # Timers outlive the widget tree otherwise, and the next tick queries a
        # DOM that no longer exists — a NoMatches traceback on quit.
        for timer in self._poll_timers:
            timer.stop()
        self._poll_timers = []

    def _table(self) -> DataTable | None:
        """None once the DOM is gone, so timer ticks during teardown are quiet."""
        found = self.query("#projects").results(DataTable)
        return next(iter(found), None)

    # -- actions ---------------------------------------------------------

    def action_rescan(self) -> None:
        self.projects = usage.order_projects(discover(self.root))
        self._refresh_git()
        self._apply_filter(self.query_one("#filter", Input).value)
        self._log(f"scanned {self.root} — {len(self.projects)} projects")

    def action_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.can_focus = True
        field.add_class("visible")
        field.focus()

    def action_close_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.value = ""
        self._hide_filter(field)

    def _hide_filter(self, field: Input) -> None:
        field.remove_class("visible")
        field.can_focus = False
        table = self._table()
        if table is not None:
            table.focus()

    def action_launch(self) -> None:
        self._launch(force=False)

    def action_force(self) -> None:
        self._launch(force=True)

    def action_restart(self) -> None:
        project = self._selected()
        if project is None:
            return
        self._log(f"⟳ {project.name}")
        # Ports are held by the processes being replaced, so skip that check.
        for check in preflight.check_project(project, check_ports=False):
            if check.level != "ok":
                self._log(f"  {check.render()}")
        self._run_launch(project, lambda: runner.restart_project(project))

    def action_doctor(self) -> None:
        project = self._selected()
        if project is None:
            return
        self._log(f"⌕ {project.name}")
        for check in preflight.check_project(project):
            self._log(f"  {check.render()}")

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
        self._reset_followers()
        self._refresh_rows()

    # -- events ----------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._hide_filter(event.input)

    # -- launching -------------------------------------------------------

    def _launch(self, *, force: bool) -> None:
        project = self._selected()
        if project is None:
            return
        self._log(f"→ {project.name}")

        checks = preflight.check_project(project)
        for check in checks:
            if check.level != "ok":
                self._log(f"  {check.render()}")
        blockers = preflight.blocking(checks)
        if blockers and not force:
            self._log("  ✗ pre-flight failed — press f to force")
            return
        if blockers and force:
            for task in project.tasks:
                if task.port is None:
                    continue
                owner = runner.free_port(task.port)
                if owner is not None:
                    self._log(f"  ■ freed port {task.port} — killed {owner.render()}")

        try:
            runner.open_editor(project)
            self._log(f"  ✓ editor {project.path}")
        except runner.LaunchError as exc:
            self._log(f"  ✗ {exc}")
        self._run_launch(project, lambda: runner.start_project(project))

    def _run_launch(self, project: Project, launch) -> None:  # noqa: ANN001 - local callable
        try:
            for result in launch():
                port = f":{result.port}" if result.port else ""
                ready = "" if result.port is None else (" ready" if result.port_ready else " (port not up yet)")
                self._log(f"  ✓ {result.task} pid {result.pid} {port}{ready}")
        except runner.LaunchError as exc:
            self._log(f"  ✗ {exc}")
        self._reset_followers()
        self._refresh_rows()

    # -- log streaming ---------------------------------------------------

    def _pump_logs(self) -> None:
        project = self._selected()
        if project is None:
            return
        if project.name != self._followed_project:
            self._reset_followers()
        for task_name, follower in self._followers.items():
            for line in follower.poll():
                self._log(f"[{task_name}] {line}")

    def _reset_followers(self) -> None:
        project = self._selected()
        self._followers = {}
        self._followed_project = project.name if project else None
        if project is None:
            return
        for task in state.for_project(project.name):
            self._followers[task.task] = logs.LogFollower(Path(task.log_path), from_end=True)

    # -- rendering -------------------------------------------------------

    def _refresh_git(self) -> None:
        self.git = gitinfo.statuses({p.name: p.path for p in self.projects})
        self._refresh_rows()

    def _apply_filter(self, needle: str) -> None:
        lowered = needle.strip().lower()
        self.matching = [p for p in self.projects if lowered in p.name.lower()] if lowered else list(self.projects)
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        table = self._table()
        if table is None:
            return
        cursor = table.cursor_row
        table.clear()
        running = {task.project for task in state.prune()}
        for project in self.matching:
            active = project.name in running
            branch = self.git.get(project.name)
            table.add_row(
                RUNNING_MARK if active else IDLE_MARK,
                project.name,
                project.stack,
                project.summary,
                branch.label() if branch is not None else "",
            )
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))

    def _selected(self) -> Project | None:
        table = self._table()
        if table is None or not self.matching or not 0 <= table.cursor_row < len(self.matching):
            return None
        return self.matching[table.cursor_row]

    def _log(self, message: str) -> None:
        self.query_one("#output", Log).write_line(message)


def run_tui(root: Path) -> None:
    LaunchpadApp(root).run()
