"""Project/task model plus `launcher.toml` parsing."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

CONFIG_FILENAME = "launcher.toml"
DEFAULT_EDITOR = "code"
PORT_WAIT_SECONDS = 30.0
PORT_POLL_INTERVAL = 0.25


class ConfigError(Exception):
    """Raised when a launcher.toml is present but unusable."""


@dataclass(frozen=True)
class Task:
    name: str
    cmd: str
    subdir: str = ""
    port: int | None = None
    open_browser: bool = False
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    stack: str
    tasks: tuple[Task, ...]
    editor: str = DEFAULT_EDITOR

    @property
    def summary(self) -> str:
        return " + ".join(task.name for task in self.tasks) if self.tasks else "—"


def load_config(project_path: Path) -> tuple[tuple[Task, ...], str | None]:
    """Read `launcher.toml`. Returns (tasks, editor_override).

    Missing file is not an error — discovery falls back to stack detection.
    """
    config_path = project_path / CONFIG_FILENAME
    if not config_path.is_file():
        return (), None

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{config_path.name}: {exc}") from exc

    editor = raw.get("editor")
    if editor is not None and not isinstance(editor, str):
        raise ConfigError(f"{config_path.name}: 'editor' must be a string")

    entries = raw.get("task", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{config_path.name}: 'task' must be a list of tables")

    tasks = tuple(_parse_task(entry, config_path.name, index) for index, entry in enumerate(entries))
    _reject_duplicate_names(tasks, config_path.name)
    return tasks, editor


def _parse_task(entry: object, filename: str, index: int) -> Task:
    where = f"{filename}: task[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table")

    name = entry.get("name")
    cmd = entry.get("cmd")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where} needs a non-empty 'name'")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ConfigError(f"{where} ({name}) needs a non-empty 'cmd'")

    port = entry.get("port")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
        raise ConfigError(f"{where} ({name}): 'port' must be an integer 1-65535")

    subdir = entry.get("cwd", "")
    if not isinstance(subdir, str):
        raise ConfigError(f"{where} ({name}): 'cwd' must be a string")
    if _escapes_project(subdir):
        raise ConfigError(f"{where} ({name}): 'cwd' must stay inside the project")

    open_browser = entry.get("open", False)
    if not isinstance(open_browser, bool):
        raise ConfigError(f"{where} ({name}): 'open' must be true or false")

    env = _parse_env(entry.get("env", {}), where, name)
    return Task(
        name=name.strip(),
        cmd=cmd.strip(),
        subdir=subdir,
        port=port,
        open_browser=open_browser,
        env=env,
    )


def _escapes_project(subdir: str) -> bool:
    """Reject any 'cwd' that could point outside the project directory.

    Checked against both path flavours on purpose: `Path("/etc").is_absolute()`
    is False on Windows (no drive letter), so a posix-style root in a config
    written on Linux would otherwise slip through and resolve drive-relative.
    """
    if subdir.startswith(("/", "\\")):
        return True
    for flavour in (PurePosixPath, PureWindowsPath):
        candidate = flavour(subdir)
        if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
            return True
    return False


def _parse_env(raw: object, where: str, task_name: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} ({task_name}): 'env' must be a table of strings")
    env: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise ConfigError(f"{where} ({task_name}): env['{key}'] must be a string")
        env[str(key)] = value
    return env


def _reject_duplicate_names(tasks: tuple[Task, ...], filename: str) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.name in seen:
            raise ConfigError(f"{filename}: duplicate task name '{task.name}'")
        seen.add(task.name)
