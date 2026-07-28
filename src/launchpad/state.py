"""Persisted record of processes launchpad started, keyed by project."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

STATE_FILENAME = "state.json"


@dataclass(frozen=True)
class RunningTask:
    project: str
    task: str
    pid: int
    create_time: float
    cmd: str
    log_path: str
    port: int | None = None

    def is_alive(self) -> bool:
        """True only if the recorded PID is still *the same* process.

        A bare `pid_exists` check is unsound: after a crash the OS can hand the
        number to something unrelated, and `stop` would then kill a stranger.
        Process creation time makes the identity check exact.
        """
        try:
            process = psutil.Process(self.pid)
            return abs(process.create_time() - self.create_time) < 1.0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False


def state_dir() -> Path:
    base = os.environ.get("LAUNCHPAD_HOME")
    if base:
        return Path(base)
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return root / "launchpad"


def log_dir() -> Path:
    return state_dir() / "logs"


def load() -> list[RunningTask]:
    path = state_dir() / STATE_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [task for task in (_from_dict(entry) for entry in raw) if task is not None]


def save(tasks: list[RunningTask]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([asdict(task) for task in tasks], indent=2)
    (directory / STATE_FILENAME).write_text(payload, encoding="utf-8")


def prune() -> list[RunningTask]:
    """Drop dead entries from disk and return what is genuinely still running."""
    alive = [task for task in load() if task.is_alive()]
    save(alive)
    return alive


def add(task: RunningTask) -> None:
    tasks = [t for t in prune() if not (t.project == task.project and t.task == task.task)]
    tasks.append(task)
    save(tasks)


def remove(project: str, task_names: list[str] | None = None) -> None:
    def keep(task: RunningTask) -> bool:
        if task.project != project:
            return True
        return task_names is not None and task.task not in task_names

    save([task for task in load() if keep(task)])


def for_project(project: str) -> list[RunningTask]:
    return [task for task in prune() if task.project == project]


def _from_dict(entry: object) -> RunningTask | None:
    if not isinstance(entry, dict):
        return None
    try:
        return RunningTask(
            project=str(entry["project"]),
            task=str(entry["task"]),
            pid=int(entry["pid"]),
            create_time=float(entry["create_time"]),
            cmd=str(entry["cmd"]),
            log_path=str(entry["log_path"]),
            port=int(entry["port"]) if entry.get("port") is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None
