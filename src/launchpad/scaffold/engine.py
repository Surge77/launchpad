"""Turn a recipe into a real project on disk."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from launchpad.scaffold.plan import Command, Download, FileSpec, Plan, Recipe, ScaffoldError, Step

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DOWNLOAD_TIMEOUT_SECONDS = 60
# Windows refuses these as filenames regardless of extension.
RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}
)


def validate_name(name: str) -> str:
    """Names reach argv and the filesystem, so they are allow-listed, not escaped."""
    candidate = name.strip()
    if not NAME_PATTERN.match(candidate):
        raise ScaffoldError(
            f"invalid project name {name!r} — use lowercase letters, digits, dash, dot or underscore, "
            "starting with a letter or digit"
        )
    if candidate.split(".")[0] in RESERVED_NAMES:
        raise ScaffoldError(f"{name!r} is a reserved device name on Windows")
    if ".." in candidate:
        raise ScaffoldError(f"invalid project name {name!r} — '..' is not allowed")
    if candidate.endswith("."):
        # Windows silently strips trailing dots, so the directory would not
        # match the name recorded in launcher.toml and git.
        raise ScaffoldError(f"invalid project name {name!r} — cannot end with '.'")
    return candidate


def build_plan(
    recipe: Recipe,
    name: str,
    root: Path,
    *,
    git: bool = True,
    github: str = "none",
) -> Plan:
    validated = validate_name(name)
    if github != "none" and not git:
        # `gh repo create --source . --push` needs a commit to push.
        raise ScaffoldError("--github requires git; drop --no-git or use --github none")

    target = (root / validated).resolve()
    if target.exists():
        if not target.is_dir():
            raise ScaffoldError(f"{target} exists and is a file")
        if any(target.iterdir()):
            raise ScaffoldError(f"{target} already exists and is not empty")
    return Plan(
        recipe=recipe,
        name=validated,
        target=target,
        steps=recipe.steps(validated),
        git=git,
        github=github,
    )


def missing_tools(recipe: Recipe) -> list[str]:
    return [tool for tool in recipe.requires if shutil.which(tool) is None]


def describe(plan: Plan) -> list[str]:
    """Human-readable plan, used by --dry-run."""
    lines = [f"{plan.recipe.title} → {plan.target}"]
    for step in plan.steps:
        if isinstance(step, Command):
            where = "../" if step.in_parent else (step.subdir or ".")
            lines.append(f"  run   [{where}] {' '.join(step.argv)}")
        elif isinstance(step, Download):
            lines.append(f"  fetch {step.url}")
        else:
            lines.append(f"  write {step.path}")
    if plan.git:
        lines.append("  run   git init + initial commit")
    if plan.github != "none":
        lines.append(f"  run   gh repo create ({plan.github})")
    return lines


def execute(plan: Plan, *, on_progress=print) -> Path:  # noqa: ANN001 - simple reporter callable
    plan.target.mkdir(parents=True, exist_ok=True)

    for step in plan.steps:
        on_progress(f"  → {step.label}")
        _run_step(step, plan)

    if plan.git:
        on_progress("  → git init + initial commit")
        _init_git(plan)
    if plan.github != "none":
        on_progress(f"  → gh repo create ({plan.github})")
        _create_github_repo(plan)
    return plan.target


def _run_step(step: Step, plan: Plan) -> None:
    if isinstance(step, Command):
        _run_command(step, plan)
    elif isinstance(step, Download):
        _download(step, plan)
    else:
        _write_file(step, plan)


def _run_command(step: Command, plan: Plan) -> None:
    executable = shutil.which(step.argv[0])
    if executable is None:
        raise ScaffoldError(f"{step.label}: '{step.argv[0]}' is not on PATH")

    cwd = plan.parent if step.in_parent else plan.target / step.subdir
    cwd.mkdir(parents=True, exist_ok=True)

    # shell=False: the project name is allow-listed, and no argument is ever
    # concatenated into a command string.
    completed = subprocess.run([executable, *step.argv[1:]], cwd=cwd, check=False)  # noqa: S603
    if completed.returncode != 0 and not step.optional:
        raise ScaffoldError(f"{step.label} failed with exit code {completed.returncode}")


def _write_file(step: FileSpec, plan: Plan) -> None:
    path = plan.target / step.path
    if path.exists() and not step.overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(step.content, encoding="utf-8")


def _download(step: Download, plan: Plan) -> None:
    if not step.url.startswith("https://"):
        raise ScaffoldError(f"{step.label}: refusing to fetch over plain HTTP")
    try:
        with urllib.request.urlopen(step.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = response.read()
    except OSError as exc:
        raise ScaffoldError(f"{step.label}: {exc}") from exc

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        _extract_safely(archive, plan.target)


def _extract_safely(archive: zipfile.ZipFile, target: Path) -> None:
    """Reject zip entries that would escape the project directory (zip slip)."""
    resolved_target = target.resolve()
    for member in archive.namelist():
        destination = (resolved_target / member).resolve()
        if not destination.is_relative_to(resolved_target):
            raise ScaffoldError(f"archive entry escapes the project directory: {member}")
    archive.extractall(resolved_target)


def _init_git(plan: Plan) -> None:
    git = shutil.which("git")
    if git is None:
        raise ScaffoldError("git is not on PATH")
    commands = (
        (git, "init", "-q", "-b", "main"),
        (git, "add", "-A"),
        (git, "commit", "-q", "-m", f"feat: scaffold {plan.name} ({plan.recipe.title})"),
    )
    for argv in commands:
        completed = subprocess.run(list(argv), cwd=plan.target, check=False)  # noqa: S603
        if completed.returncode != 0:
            raise ScaffoldError(f"git step failed: {' '.join(argv[1:])}")


def _create_github_repo(plan: Plan) -> None:
    gh = shutil.which("gh")
    if gh is None:
        raise ScaffoldError("gh is not on PATH — install the GitHub CLI or use --github none")
    argv = [
        gh, "repo", "create", plan.name,
        f"--{plan.github}",
        "--source", ".",
        "--remote", "origin",
        "--description", plan.recipe.summary,
        "--push",
    ]
    completed = subprocess.run(argv, cwd=plan.target, check=False)  # noqa: S603
    if completed.returncode != 0:
        raise ScaffoldError("gh repo create failed")
