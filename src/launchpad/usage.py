"""Frecency: projects you launch often and recently float to the top."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from launchpad.config import Project
from launchpad.state import state_dir

USAGE_FILENAME = "usage.json"
SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class Usage:
    count: int = 0
    last_launched: float = 0.0

    def score(self, now: float | None = None) -> float:
        """Launch count damped by age, so a stale favourite yields to today's work."""
        if self.count == 0:
            return 0.0
        moment = time.time() if now is None else now
        days_idle = max(0.0, (moment - self.last_launched) / SECONDS_PER_DAY)
        return self.count / (1.0 + days_idle)


def _path() -> Path:
    return state_dir() / USAGE_FILENAME


def load() -> dict[str, Usage]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    usage: dict[str, Usage] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            usage[str(name)] = Usage(count=int(entry["count"]), last_launched=float(entry["last_launched"]))
        except (KeyError, TypeError, ValueError):
            continue
    return usage


def save(usage: dict[str, Usage]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {name: {"count": entry.count, "last_launched": entry.last_launched} for name, entry in usage.items()}
    _path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record_launch(project_name: str) -> None:
    usage = load()
    previous = usage.get(project_name, Usage())
    usage[project_name] = Usage(count=previous.count + 1, last_launched=time.time())
    save(usage)


def order_projects(projects: list[Project]) -> list[Project]:
    """Frecency first, then most-recently-touched on disk."""
    usage = load()
    now = time.time()

    def sort_key(project: Project) -> tuple[float, float]:
        score = usage.get(project.name, Usage()).score(now)
        try:
            mtime = project.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (-score, -mtime)

    return sorted(projects, key=sort_key)
