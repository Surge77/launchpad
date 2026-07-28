from __future__ import annotations

import time
from pathlib import Path

import pytest

from launchpad import usage
from launchpad.config import Project

DAY = 86_400.0


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_HOME", str(tmp_path / "home"))


def make_project(tmp_path: Path, name: str) -> Project:
    path = tmp_path / name
    path.mkdir(exist_ok=True)
    return Project(name=name, path=path, stack="TS", tasks=())


def test_unused_project_scores_zero() -> None:
    assert usage.Usage().score() == 0.0


def test_score_rises_with_launch_count() -> None:
    now = time.time()
    few = usage.Usage(count=2, last_launched=now)
    many = usage.Usage(count=20, last_launched=now)

    assert many.score(now) > few.score(now)


def test_score_decays_with_idle_days() -> None:
    now = time.time()
    fresh = usage.Usage(count=5, last_launched=now)
    stale = usage.Usage(count=5, last_launched=now - 30 * DAY)

    assert fresh.score(now) > stale.score(now)


def test_recent_single_launch_beats_old_favourite() -> None:
    """Today's work should outrank last month's habit."""
    now = time.time()
    today = usage.Usage(count=1, last_launched=now)
    ancient = usage.Usage(count=10, last_launched=now - 60 * DAY)

    assert today.score(now) > ancient.score(now)


def test_record_launch_increments_count() -> None:
    usage.record_launch("demo")
    usage.record_launch("demo")

    assert usage.load()["demo"].count == 2


def test_order_projects_puts_launched_project_first(tmp_path: Path) -> None:
    never = make_project(tmp_path, "never-run")
    often = make_project(tmp_path, "often-run")
    usage.record_launch("often-run")

    ordered = usage.order_projects([never, often])

    assert [project.name for project in ordered] == ["often-run", "never-run"]


def test_order_projects_falls_back_to_mtime(tmp_path: Path) -> None:
    older = make_project(tmp_path, "older")
    newer = make_project(tmp_path, "newer")
    import os

    os.utime(older.path, (1_000_000, 1_000_000))
    os.utime(newer.path, (2_000_000, 2_000_000))

    ordered = usage.order_projects([older, newer])

    assert [project.name for project in ordered] == ["newer", "older"]


def test_corrupt_usage_file_is_ignored() -> None:
    directory = usage.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / usage.USAGE_FILENAME).write_text("{not json", encoding="utf-8")

    assert usage.load() == {}


def test_malformed_entries_are_skipped() -> None:
    directory = usage.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / usage.USAGE_FILENAME).write_text('{"a": {"count": "x"}, "b": {}}', encoding="utf-8")

    assert usage.load() == {}
