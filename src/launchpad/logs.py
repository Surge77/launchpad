"""Reading task logs: last-N lines and incremental follow."""

from __future__ import annotations

from pathlib import Path

TAIL_BLOCK_SIZE = 8192
DEFAULT_TAIL_LINES = 200


def tail(path: Path, limit: int = DEFAULT_TAIL_LINES) -> list[str]:
    """Last `limit` lines, read from the end so a 200 MB log stays cheap."""
    if limit <= 0:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            block = min(TAIL_BLOCK_SIZE, size)
            data = b""
            while size > 0 and data.count(b"\n") <= limit:
                size = max(0, size - block)
                handle.seek(size)
                data = handle.read(block) + data
                if size == 0:
                    break
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-limit:]


class LogFollower:
    """Yields lines appended to a file since the last call."""

    def __init__(self, path: Path, *, from_end: bool = False) -> None:
        self.path = path
        self._offset = self._size() if from_end else 0
        self._partial = ""

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def poll(self) -> list[str]:
        size = self._size()
        if size < self._offset:  # file was truncated or replaced on relaunch
            self._offset = 0
            self._partial = ""
        if size == self._offset:
            return []

        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
        except OSError:
            return []

        self._offset += len(chunk)
        text = self._partial + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self._partial = lines.pop()
        return [line.rstrip("\r") for line in lines]
