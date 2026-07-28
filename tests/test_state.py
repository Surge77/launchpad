from __future__ import annotations

import os
import time
from pathlib import Path

import psutil
import pytest

from launchpad import state


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAUNCHPAD_HOME", str(tmp_path / "launchpad"))


def current_process_task(**overrides: object) -> state.RunningTask:
    pid = os.getpid()
    defaults: dict[str, object] = {
        "project": "demo",
        "task": "web",
        "pid": pid,
        "create_time": psutil.Process(pid).create_time(),
        "cmd": "pnpm dev",
        "log_path": "demo-web.log",
        "port": 3000,
    }
    return state.RunningTask(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_load_returns_empty_when_no_state_file() -> None:
    assert state.load() == []


def test_save_then_load_roundtrip() -> None:
    task = current_process_task()

    state.save([task])

    assert state.load() == [task]


def test_is_alive_true_for_live_process_with_matching_create_time() -> None:
    assert current_process_task().is_alive() is True


def test_is_alive_false_when_create_time_differs() -> None:
    """PID reuse guard: same PID, different process identity."""
    stale = current_process_task(create_time=time.time() - 99_999)

    assert stale.is_alive() is False


def test_is_alive_false_for_nonexistent_pid() -> None:
    unused_pid = max(p.pid for p in psutil.process_iter()) + 10_000

    assert current_process_task(pid=unused_pid).is_alive() is False


def test_prune_drops_dead_entries_from_disk() -> None:
    alive = current_process_task()
    dead = current_process_task(project="ghost", create_time=1.0)
    state.save([alive, dead])

    remaining = state.prune()

    assert [task.project for task in remaining] == ["demo"]
    assert [task.project for task in state.load()] == ["demo"]


def test_add_replaces_same_project_task_pair() -> None:
    state.add(current_process_task(cmd="old"))
    state.add(current_process_task(cmd="new"))

    tasks = state.load()

    assert len(tasks) == 1
    assert tasks[0].cmd == "new"


def test_add_keeps_distinct_tasks_of_same_project() -> None:
    state.add(current_process_task(task="web"))
    state.add(current_process_task(task="db"))

    assert sorted(task.task for task in state.load()) == ["db", "web"]


def test_remove_deletes_all_tasks_for_project() -> None:
    state.save([current_process_task(task="web"), current_process_task(task="db")])

    state.remove("demo")

    assert state.load() == []


def test_for_project_filters_by_name() -> None:
    state.save([current_process_task(project="a"), current_process_task(project="b")])

    assert [task.project for task in state.for_project("b")] == ["b"]


def test_corrupt_state_file_is_ignored() -> None:
    directory = state.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / state.STATE_FILENAME).write_text("{not json", encoding="utf-8")

    assert state.load() == []


def test_entries_missing_required_keys_are_skipped() -> None:
    directory = state.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / state.STATE_FILENAME).write_text('[{"project": "x"}]', encoding="utf-8")

    assert state.load() == []
