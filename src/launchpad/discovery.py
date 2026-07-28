"""Find git repos under a root and infer how to run each one."""

from __future__ import annotations

import json
from pathlib import Path

from launchpad.config import CONFIG_FILENAME, Project, Task, load_config

SCAN_DEPTH = 2
SKIP_DIRS = frozenset({"node_modules", ".git", "target", "dist", "build", "__pycache__", ".venv", "venv"})

# Framework -> conventional dev port. Only used when launcher.toml gives no port.
NEXT_PORT = 3000
VITE_PORT = 5173
UVICORN_PORT = 8000
DJANGO_PORT = 8000
SPRING_PORT = 8080

_PACKAGE_MANAGER_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("bun.lockb", "bun"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)


def discover(root: Path) -> list[Project]:
    """Scan `root` (and one level of subdirs) for git repos, newest-touched first."""
    projects = [_build_project(path) for path in _find_repos(root)]
    projects.sort(key=lambda project: project.path.stat().st_mtime, reverse=True)
    return projects


def _find_repos(root: Path, depth: int = SCAN_DEPTH) -> list[Path]:
    if depth < 0 or not root.is_dir():
        return []
    repos: list[Path] = []
    for entry in _safe_iterdir(root):
        if not entry.is_dir() or entry.name in SKIP_DIRS:
            continue
        if (entry / ".git").exists():
            repos.append(entry)
            continue
        repos.extend(_find_repos(entry, depth - 1))
    return repos


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def _build_project(path: Path) -> Project:
    configured, editor = load_config(path)
    stack = detect_stack(path)

    if configured:
        tasks = configured
    elif stack != "-":
        tasks = detect_tasks(path, stack)
    else:
        # Monorepo: manifests live one level down (relay/gateway, relay/web).
        stack, tasks = _detect_workspace(path)

    project = Project(name=path.name, path=path, stack=stack, tasks=tasks)
    return project if editor is None else Project(**{**vars(project), "editor": editor})


def _detect_workspace(path: Path) -> tuple[str, tuple[Task, ...]]:
    """Detect stacks/tasks in immediate subdirectories of a repo root."""
    stacks: list[str] = []
    tasks: list[Task] = []
    for entry in _safe_iterdir(path):
        if not entry.is_dir() or entry.name in SKIP_DIRS:
            continue
        member_stack = detect_stack(entry)
        if member_stack == "-":
            continue
        stacks.append(member_stack)
        tasks.extend(
            # Name the task after its directory — "gateway" reads better than a
            # second task also called "run".
            Task(
                name=entry.name,
                cmd=task.cmd,
                subdir=entry.name if not task.subdir else f"{entry.name}/{task.subdir}",
                port=task.port,
                open_browser=task.open_browser,
                env=task.env,
            )
            for task in detect_tasks(entry, member_stack)
        )

    if not stacks:
        return "-", ()
    return "+".join(dict.fromkeys(stacks)), tuple(tasks)


def detect_stack(path: Path) -> str:
    if (path / "package.json").is_file():
        return "TS" if _has_typescript(path) else "JS"
    if (path / "Cargo.toml").is_file():
        return "Rust"
    if (path / "go.mod").is_file():
        return "Go"
    if (path / "pom.xml").is_file() or _first_existing(path, "build.gradle", "build.gradle.kts"):
        return "Java"
    if (path / "pyproject.toml").is_file() or (path / "requirements.txt").is_file():
        return "Py"
    return "-"


def detect_tasks(path: Path, stack: str) -> tuple[Task, ...]:
    detectors = {
        "TS": _node_tasks,
        "JS": _node_tasks,
        "Rust": lambda p: (Task(name="run", cmd="cargo run"),),
        "Go": lambda p: (Task(name="run", cmd="go run ./..."),),
        "Java": _java_tasks,
        "Py": _python_tasks,
    }
    detector = detectors.get(stack)
    return detector(path) if detector else ()


def _has_typescript(path: Path) -> bool:
    return (path / "tsconfig.json").is_file()


def _first_existing(path: Path, *names: str) -> Path | None:
    return next((path / name for name in names if (path / name).is_file()), None)


def _read_package_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _node_tasks(path: Path) -> tuple[Task, ...]:
    package = _read_package_json(path)
    scripts = package.get("scripts")
    scripts = scripts if isinstance(scripts, dict) else {}
    script = next((name for name in ("dev", "start", "serve") if name in scripts), None)
    if script is None:
        return ()

    manager = _package_manager(path)
    command = f"{manager} run {script}" if manager == "npm" else f"{manager} {script}"
    return (
        Task(
            name=script,
            cmd=command,
            port=_node_port(package, str(scripts.get(script, ""))),
            open_browser=True,
        ),
    )


def _package_manager(path: Path) -> str:
    for lockfile, manager in _PACKAGE_MANAGER_LOCKFILES:
        if (path / lockfile).is_file():
            return manager
    return "npm"


def _node_port(package: dict[str, object], script_body: str) -> int | None:
    dependencies = _all_dependency_names(package)
    if "next" in dependencies or "next" in script_body:
        return NEXT_PORT
    if "vite" in dependencies or "vite" in script_body:
        return VITE_PORT
    return None


def _all_dependency_names(package: dict[str, object]) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        section = package.get(key)
        if isinstance(section, dict):
            names.update(str(name) for name in section)
    return names


def _java_tasks(path: Path) -> tuple[Task, ...]:
    if _first_existing(path, "build.gradle", "build.gradle.kts"):
        wrapper = "gradlew.bat" if (path / "gradlew.bat").is_file() else "./gradlew"
        return (Task(name="bootRun", cmd=f"{wrapper} bootRun", port=SPRING_PORT, open_browser=False),)
    if not (path / "pom.xml").is_file():
        return ()
    pom = _read_text(path / "pom.xml")
    if "spring-boot" in pom:
        return (Task(name="bootRun", cmd="mvn spring-boot:run", port=SPRING_PORT),)
    return (Task(name="build", cmd="mvn -q compile"),)


def _python_tasks(path: Path) -> tuple[Task, ...]:
    manifest = _read_text(path / "pyproject.toml") + _read_text(path / "requirements.txt")
    if "fastapi" in manifest:
        return (Task(name="api", cmd="uvicorn main:app --reload", port=UVICORN_PORT, open_browser=True),)
    if "django" in manifest:
        return (Task(name="web", cmd="python manage.py runserver", port=DJANGO_PORT, open_browser=True),)
    if "flask" in manifest:
        return (Task(name="web", cmd="flask run", port=5000, open_browser=True),)
    return ()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return ""


def config_hint(project: Project) -> str:
    """Message shown when a project has no runnable tasks."""
    return f"no tasks detected — add {CONFIG_FILENAME} to {project.path}"
