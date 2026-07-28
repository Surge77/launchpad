"""Pre-launch checks: the reasons a dev server usually fails to come up."""

from __future__ import annotations

import shlex
import shutil
import socket
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import psutil

from launchpad.config import Project

Level = Literal["ok", "warn", "error"]

ENV_EXAMPLE_NAMES = (".env.example", ".env.sample", ".env.template")
PROBE_TIMEOUT_SECONDS = 0.3
MARK = {"ok": "✓", "warn": "!", "error": "✗"}


@dataclass(frozen=True)
class Check:
    level: Level
    message: str

    def render(self) -> str:
        return f"{MARK[self.level]} {self.message}"


@dataclass(frozen=True)
class PortOwner:
    pid: int
    name: str

    def render(self) -> str:
        return f"pid {self.pid} ({self.name})"


def check_project(
    project: Project,
    *,
    check_ports: bool = True,
    listeners: dict[int, PortOwner] | None = None,
) -> list[Check]:
    """Audit a project before launch.

    `check_ports=False` is for restarts: the port is expected to be held by the
    very process being replaced, so reporting it as a conflict is noise.

    `listeners` lets a caller auditing many projects enumerate sockets once
    instead of once per task.
    """
    checks: list[Check] = []
    if not project.tasks:
        return [Check("error", f"no tasks — add launcher.toml to {project.path}")]

    ports = listeners if listeners is not None else listening_ports()
    checks.extend(_task_checks(project, check_ports=check_ports, listeners=ports))
    checks.extend(_env_checks(project))
    return checks


def _task_checks(project: Project, *, check_ports: bool, listeners: dict[int, PortOwner]) -> list[Check]:
    checks: list[Check] = []
    for task in project.tasks:
        checks.append(_executable_check(task.cmd, task.name))

        cwd = project.path / task.subdir
        if not cwd.is_dir():
            checks.append(Check("error", f"{task.name}: cwd missing — {cwd}"))

        if task.port is not None and check_ports:
            checks.append(_port_check(task.name, task.port, listeners))
    return checks


@lru_cache(maxsize=256)
def _resolve(executable: str) -> str | None:
    """Cached PATH lookup.

    Auditing a workspace checks the same handful of tools (npm, mvn, uv) once
    per project, and each `shutil.which` miss stats every PATH entry against
    every PATHEXT. Cached for the process lifetime, so a tool installed while
    the TUI is open is not picked up until restart — an acceptable trade.
    """
    return shutil.which(executable)


def _executable_check(cmd: str, task_name: str) -> Check:
    argv = shlex.split(cmd, posix=sys.platform != "win32")
    if not argv:
        return Check("error", f"{task_name}: empty command")
    executable = argv[0].strip('"')
    if _resolve(executable) is None:
        return Check("error", f"{task_name}: '{executable}' not on PATH")
    return Check("ok", f"{task_name}: {executable} found")


def _port_check(task_name: str, port: int, listeners: dict[int, PortOwner]) -> Check:
    owner = port_owner(port, listeners)
    if owner is not None:
        return Check("error", f"{task_name}: port {port} already held by {owner.render()}")
    if listeners:
        # A populated map enumerated every listening socket, so absence proves
        # the port is free. Probing anyway costs a full connect timeout per port
        # on Windows — 300ms each, which dominated `launch doctor`.
        return Check("ok", f"{task_name}: port {port} free")
    # Empty map means enumeration was denied (unprivileged macOS). Fall back to
    # a probe: reporting a busy port as free would hand the user an EADDRINUSE.
    if is_listening(port):
        return Check("error", f"{task_name}: port {port} already in use (owner unknown — needs root to identify)")
    return Check("ok", f"{task_name}: port {port} free")


def is_listening(port: int, host: str = "127.0.0.1") -> bool:
    """True if something accepts TCP connections on `port`. Needs no privileges."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(PROBE_TIMEOUT_SECONDS)
        return probe.connect_ex((host, port)) == 0


def _env_checks(project: Project) -> list[Check]:
    checks: list[Check] = []
    for directory in _task_directories(project):
        example = next((directory / name for name in ENV_EXAMPLE_NAMES if (directory / name).is_file()), None)
        if example is None:
            continue
        if (directory / ".env").is_file():
            checks.append(Check("ok", f"{_relative(project, directory)}: .env present"))
        else:
            checks.append(
                Check("warn", f"{_relative(project, directory)}: {example.name} exists but .env is missing")
            )
    return checks


def _task_directories(project: Project) -> list[Path]:
    directories = [project.path]
    for task in project.tasks:
        candidate = project.path / task.subdir
        if candidate.is_dir() and candidate not in directories:
            directories.append(candidate)
    return directories


def _relative(project: Project, path: Path) -> str:
    try:
        relative = path.relative_to(project.path)
    except ValueError:
        return path.name
    return str(relative) if str(relative) != "." else project.name


def port_owner(port: int, host_ports: dict[int, PortOwner] | None = None) -> PortOwner | None:
    """Which process is listening on `port`, if any."""
    listeners = host_ports if host_ports is not None else listening_ports()
    return listeners.get(port)


def listening_ports() -> dict[int, PortOwner]:
    """Map listening port -> owning process.

    Ports owned by other users raise AccessDenied on Windows; those come back
    as 'unknown' rather than being silently reported free, because "free" would
    send the user into a confusing EADDRINUSE crash.
    """
    owners: dict[int, PortOwner] = {}
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return owners

    for connection in connections:
        if connection.status != psutil.CONN_LISTEN or connection.laddr is None:
            continue
        port = getattr(connection.laddr, "port", None)
        if port is None or port in owners:
            continue
        owners[port] = PortOwner(pid=connection.pid or -1, name=_process_name(connection.pid))
    return owners


def _process_name(pid: int | None) -> str:
    if pid is None:
        return "unknown"
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "unknown"


def blocking(checks: list[Check]) -> list[Check]:
    return [check for check in checks if check.level == "error"]
