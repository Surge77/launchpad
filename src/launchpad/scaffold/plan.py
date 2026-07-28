"""Types describing what creating a project involves."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


class ScaffoldError(Exception):
    """Raised when a project cannot be created."""


@dataclass(frozen=True)
class Command:
    """An external tool invocation. Runs without a shell."""

    label: str
    argv: tuple[str, ...]
    subdir: str = ""
    in_parent: bool = False
    optional: bool = False


@dataclass(frozen=True)
class FileSpec:
    """A file written into the new project."""

    path: str
    content: str
    overwrite: bool = True

    @property
    def label(self) -> str:
        return f"write {self.path}"


@dataclass(frozen=True)
class Download:
    """A zip fetched over HTTPS and extracted into the project."""

    label: str
    url: str


Step = Command | FileSpec | Download


StepBuilder = Callable[[str], list[Step]]


@dataclass(frozen=True)
class Recipe:
    key: str
    title: str
    summary: str
    requires: tuple[str, ...]
    build: StepBuilder | None = field(repr=False, default=None)

    def steps(self, name: str) -> tuple[Step, ...]:
        if self.build is None:
            raise ScaffoldError(f"recipe {self.key} has no builder")
        return tuple(self.build(name))


@dataclass(frozen=True)
class Plan:
    recipe: Recipe
    name: str
    target: Path
    steps: tuple[Step, ...]
    git: bool = True
    github: str = "none"

    @property
    def parent(self) -> Path:
        return self.target.parent
