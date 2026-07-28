"""Spawn, wait on, and tear down a project's dev processes."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import psutil

from launchpad import preflight, state, usage
from launchpad.config import PORT_POLL_INTERVAL, PORT_WAIT_SECONDS, Project, Task

TERMINATE_GRACE_SECONDS = 5.0
PORT_RELEASE_SECONDS = 10.0


class LaunchError(Exception):
    """Raised when a task cannot be started."""


@dataclass(frozen=True)
class LaunchResult:
    task: str
    pid: int
    port: int | None
    url: str | None
    log_path: Path
    port_ready: bool


def start_project(project: Project, *, wait_for_ports: bool = True) -> list[LaunchResult]:
    if not project.tasks:
        raise LaunchError(f"{project.name}: no tasks to run")
    results = [start_task(project, task, wait_for_port=wait_for_ports) for task in project.tasks]
    usage.record_launch(project.name)
    return results


def restart_project(project: Project, *, wait_for_ports: bool = True) -> list[LaunchResult]:
    """Stop everything tracked for the project, then start it again.

    Ports need a moment to leave TIME_WAIT after teardown, otherwise the
    relaunch hits EADDRINUSE against its own corpse.
    """
    stop_project(project.name)
    for task in project.tasks:
        if task.port is not None:
            _wait_for_port_release(task.port)
    return start_project(project, wait_for_ports=wait_for_ports)


def _wait_for_port_release(port: int, timeout: float = PORT_RELEASE_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_port_open(port):
            return True
        time.sleep(PORT_POLL_INTERVAL)
    return False


def free_port(port: int) -> preflight.PortOwner | None:
    """Kill whatever is listening on `port`. Destructive — callers must opt in."""
    owner = preflight.port_owner(port)
    if owner is None or owner.pid <= 0:
        return None
    _kill_tree(owner.pid)
    _wait_for_port_release(port)
    return owner


def start_task(project: Project, task: Task, *, wait_for_port: bool = True) -> LaunchResult:
    already = next((t for t in state.for_project(project.name) if t.task == task.name), None)
    if already is not None:
        raise LaunchError(f"{project.name}/{task.name} already running (pid {already.pid})")

    cwd = (project.path / task.subdir).resolve()
    if not cwd.is_dir():
        raise LaunchError(f"{project.name}/{task.name}: cwd does not exist: {cwd}")

    argv = resolve_argv(task.cmd)
    log_path = _log_path(project.name, task.name)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, **task.env}
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write(f"$ {task.cmd}\n[cwd] {cwd}\n\n")
        log_file.flush()
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is a resolved list, shell=False
                argv,
                cwd=cwd,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=_creation_flags(),
                start_new_session=not _is_windows(),
            )
        except OSError as exc:
            raise LaunchError(f"{project.name}/{task.name}: {exc}") from exc

    try:
        create_time = psutil.Process(process.pid).create_time()
    except psutil.Error:
        create_time = time.time()

    state.add(
        state.RunningTask(
            project=project.name,
            task=task.name,
            pid=process.pid,
            create_time=create_time,
            cmd=task.cmd,
            log_path=str(log_path),
            port=task.port,
        )
    )

    port_ready = False
    if task.port is not None and wait_for_port:
        port_ready = wait_for_port_open(task.port)

    url = f"http://localhost:{task.port}" if task.port is not None else None
    if url is not None and task.open_browser and port_ready:
        webbrowser.open(url)

    return LaunchResult(
        task=task.name,
        pid=process.pid,
        port=task.port,
        url=url,
        log_path=log_path,
        port_ready=port_ready,
    )


def resolve_argv(cmd: str) -> list[str]:
    """Split a command string and resolve the executable on PATH.

    Deliberately avoids `shell=True`: `cmd` comes from a repo's launcher.toml,
    and a shell would let `pnpm dev && anything` run as a second command.
    Resolving argv[0] via `which` is what makes Windows `.cmd` shims work
    without one.
    """
    argv = shlex.split(cmd, posix=not _is_windows())
    if not argv:
        raise LaunchError("empty command")
    argv = [part.strip('"') for part in argv]
    executable = shutil.which(argv[0])
    if executable is None:
        raise LaunchError(f"command not found on PATH: {argv[0]}")
    return [executable, *argv[1:]]


def wait_for_port_open(port: int, timeout: float = PORT_WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_port_open(port):
            return True
        time.sleep(PORT_POLL_INTERVAL)
    return False


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def stop_project(project_name: str) -> list[str]:
    """Kill every tracked process for a project, children first."""
    stopped: list[str] = []
    for running in state.for_project(project_name):
        if _kill_tree(running.pid):
            stopped.append(running.task)
    state.remove(project_name)
    return stopped


def _kill_tree(pid: int) -> bool:
    try:
        parent = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

    processes = parent.children(recursive=True) + [parent]
    for process in processes:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    _, alive = psutil.wait_procs(processes, timeout=TERMINATE_GRACE_SECONDS)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return True


def open_editor(project: Project) -> None:
    editor = os.environ.get("LAUNCHPAD_EDITOR", project.editor)
    executable = shutil.which(editor)
    if executable is None:
        raise LaunchError(f"editor not found on PATH: {editor}")
    subprocess.Popen(  # noqa: S603 - executable resolved via which, shell=False
        [executable, str(project.path)],
        cwd=project.path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creation_flags(),
    )


def _log_path(project_name: str, task_name: str) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in f"{project_name}-{task_name}")
    return state.log_dir() / f"{safe}.log"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _creation_flags() -> int:
    # New process group so Ctrl-C in the launcher does not tear down dev servers.
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if _is_windows() else 0
