from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from launchpad import gitinfo


def test_parses_clean_tracked_branch() -> None:
    status = gitinfo.parse_status("## main...origin/main\n")

    assert status.branch == "main"
    assert status.dirty == 0
    assert status.ahead == 0
    assert status.behind == 0


def test_parses_untracked_local_branch() -> None:
    assert gitinfo.parse_status("## feature/x\n").branch == "feature/x"


def test_counts_dirty_files() -> None:
    output = "## main...origin/main\n M src/a.py\n?? new.txt\nA  staged.py\n"

    assert gitinfo.parse_status(output).dirty == 3


def test_parses_ahead_and_behind() -> None:
    status = gitinfo.parse_status("## main...origin/main [ahead 2, behind 3]\n")

    assert status.ahead == 2
    assert status.behind == 3


def test_parses_ahead_only() -> None:
    status = gitinfo.parse_status("## main...origin/main [ahead 7]\n")

    assert (status.ahead, status.behind) == (7, 0)


def test_repo_with_no_commits_reports_plain_branch_name() -> None:
    assert gitinfo.parse_status("## No commits yet on main\n").branch == "main"


def test_detached_head_is_labelled() -> None:
    assert gitinfo.parse_status("## HEAD (no branch)\n").branch == "detached"


def test_empty_output_is_detached() -> None:
    assert gitinfo.parse_status("").branch == "detached"


def test_label_shows_dirty_and_ahead_markers() -> None:
    label = gitinfo.GitStatus(branch="main", dirty=3, ahead=1).label()

    assert label == "main *3 ↑1"


def test_label_is_plain_when_clean() -> None:
    assert gitinfo.GitStatus(branch="develop", dirty=0).label() == "develop"


def test_status_returns_none_for_non_repo(tmp_path: Path) -> None:
    assert gitinfo.status(tmp_path) is None


def test_status_reads_a_real_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "file.txt").write_text("hi", encoding="utf-8")

    status = gitinfo.status(tmp_path)

    assert status is not None
    assert status.branch == "main"
    assert status.dirty == 1


def test_statuses_returns_empty_for_no_paths() -> None:
    assert gitinfo.statuses({}) == {}


def test_statuses_skips_non_repos(tmp_path: Path) -> None:
    (tmp_path / "not-a-repo").mkdir()

    assert gitinfo.statuses({"not-a-repo": tmp_path / "not-a-repo"}) == {}


def test_statuses_maps_names_to_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitinfo, "status", lambda path: gitinfo.GitStatus(branch=path.name, dirty=0))

    results = gitinfo.statuses({"a": tmp_path / "a", "b": tmp_path / "b"})

    assert {name: item.branch for name, item in results.items()} == {"a": "a", "b": "b"}
