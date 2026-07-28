from __future__ import annotations

from pathlib import Path

from launchpad import logs


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_tail_of_missing_file_is_empty(tmp_path: Path) -> None:
    assert logs.tail(tmp_path / "nope.log") == []


def test_tail_returns_all_lines_when_file_is_short(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "one\ntwo\nthree\n")

    assert logs.tail(log) == ["one", "two", "three"]


def test_tail_returns_only_last_n_lines(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "\n".join(str(number) for number in range(1000)) + "\n")

    assert logs.tail(log, 3) == ["997", "998", "999"]


def test_tail_with_zero_limit_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "one\n")

    assert logs.tail(log, 0) == []


def test_tail_handles_file_without_trailing_newline(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "one\ntwo")

    assert logs.tail(log, 2) == ["one", "two"]


def test_follower_from_start_reads_existing_content(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "existing\n")

    assert logs.LogFollower(log).poll() == ["existing"]


def test_follower_from_end_skips_existing_content(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "existing\n")

    assert logs.LogFollower(log, from_end=True).poll() == []


def test_follower_yields_appended_lines(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "first\n")
    follower = logs.LogFollower(log, from_end=True)

    with log.open("a", encoding="utf-8") as handle:
        handle.write("second\nthird\n")

    assert follower.poll() == ["second", "third"]


def test_follower_returns_nothing_when_unchanged(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "first\n")
    follower = logs.LogFollower(log)
    follower.poll()

    assert follower.poll() == []


def test_follower_holds_partial_line_until_newline_arrives(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "")
    follower = logs.LogFollower(log)

    with log.open("a", encoding="utf-8") as handle:
        handle.write("half")
    assert follower.poll() == []

    with log.open("a", encoding="utf-8") as handle:
        handle.write("-done\n")
    assert follower.poll() == ["half-done"]


def test_follower_recovers_when_log_is_truncated_on_relaunch(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    write(log, "old and long content\n")
    follower = logs.LogFollower(log, from_end=True)

    write(log, "new\n")  # relaunch truncates the log

    assert follower.poll() == ["new"]


def test_follower_strips_carriage_returns(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    log.write_bytes(b"line\r\n")

    assert logs.LogFollower(log).poll() == ["line"]


def test_follower_on_missing_file_is_quiet(tmp_path: Path) -> None:
    assert logs.LogFollower(tmp_path / "nope.log").poll() == []
