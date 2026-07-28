"""Branch and dirty-file counts, fetched in parallel so 30 repos stay fast."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT_SECONDS = 3.0
MAX_WORKERS = 12
DETACHED_LABEL = "detached"
NO_COMMITS_PREFIX = "No commits yet on "


@dataclass(frozen=True)
class GitStatus:
    branch: str
    dirty: int
    ahead: int = 0
    behind: int = 0

    def label(self) -> str:
        suffix = f" *{self.dirty}" if self.dirty else ""
        if self.ahead:
            suffix += f" ↑{self.ahead}"
        if self.behind:
            suffix += f" ↓{self.behind}"
        return f"{self.branch}{suffix}"


def status(path: Path) -> GitStatus | None:
    """Read one repo's status. Returns None if git is unavailable or the call fails."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            ["git", "-C", str(path), "status", "--porcelain=v1", "-b"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_status(completed.stdout)


def parse_status(output: str) -> GitStatus:
    lines = output.splitlines()
    header = next((line for line in lines if line.startswith("## ")), "")
    dirty = sum(1 for line in lines if line and not line.startswith("## "))
    branch, ahead, behind = _parse_header(header)
    return GitStatus(branch=branch, dirty=dirty, ahead=ahead, behind=behind)


def _parse_header(header: str) -> tuple[str, int, int]:
    if not header:
        return (DETACHED_LABEL, 0, 0)

    body = header[3:].strip()
    if body.startswith("HEAD ("):
        return (DETACHED_LABEL, 0, 0)

    # A repo with no commits reports "## No commits yet on main".
    if body.startswith(NO_COMMITS_PREFIX):
        body = body[len(NO_COMMITS_PREFIX) :].strip()

    tracking = ""
    if " [" in body and body.endswith("]"):
        body, tracking = body.split(" [", 1)
        tracking = tracking.rstrip("]")

    branch = body.split("...", 1)[0].strip() or DETACHED_LABEL
    return (branch, _tracking_count(tracking, "ahead"), _tracking_count(tracking, "behind"))


def _tracking_count(tracking: str, keyword: str) -> int:
    for part in tracking.split(","):
        part = part.strip()
        if part.startswith(keyword):
            digits = part[len(keyword) :].strip()
            return int(digits) if digits.isdigit() else 0
    return 0


def statuses(paths: dict[str, Path]) -> dict[str, GitStatus]:
    """Fetch many repos concurrently — git is I/O bound, so threads are enough."""
    if not paths:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(paths))) as pool:
        results = pool.map(status, paths.values())
    return {name: result for name, result in zip(paths.keys(), results) if result is not None}
