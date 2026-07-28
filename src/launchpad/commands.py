"""Command implementations. `cli.py` owns argument parsing and dispatch."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from launchpad import gitinfo, logs, preflight, runner, scaffold, state, usage
from launchpad.config import Project
from launchpad.discovery import discover

FOLLOW_INTERVAL_SECONDS = 0.4
RUNNING_MARK = "●"
IDLE_MARK = "○"


def load_projects(root: Path) -> list[Project]:
    return usage.order_projects(discover(root))


def find(root: Path, name: str) -> Project | None:
    projects = load_projects(root)
    exact = next((p for p in projects if p.name.lower() == name.lower()), None)
    if exact is not None:
        return exact
    matches = [p for p in projects if name.lower() in p.name.lower()]
    return matches[0] if len(matches) == 1 else None


def cmd_ls(root: Path, *, as_json: bool = False, with_git: bool = False) -> int:
    projects = load_projects(root)
    running = {task.project for task in state.prune()}
    git = gitinfo.statuses({p.name: p.path for p in projects}) if with_git else {}

    if as_json:
        print(json.dumps([_project_payload(p, p.name in running, git.get(p.name)) for p in projects], indent=2))
        return 0

    for project in projects:
        mark = RUNNING_MARK if project.name in running else IDLE_MARK
        branch = git.get(project.name)
        suffix = f"  [{branch.label()}]" if branch is not None else ""
        print(f"{mark} {project.name:<30} {project.stack:<6} {project.summary}{suffix}")
    print(f"\n{len(projects)} projects under {root}")
    return 0


def _project_payload(project: Project, running: bool, git: gitinfo.GitStatus | None) -> dict[str, object]:
    return {
        "name": project.name,
        "path": str(project.path),
        "stack": project.stack,
        "running": running,
        "tasks": [
            {"name": task.name, "cmd": task.cmd, "cwd": task.subdir, "port": task.port}
            for task in project.tasks
        ],
        "git": None if git is None else {"branch": git.branch, "dirty": git.dirty, "ahead": git.ahead},
    }


def cmd_up(root: Path, names: list[str], *, editor: bool, wait: bool, force: bool) -> int:
    failures = 0
    for name in names:
        project = find(root, name)
        if project is None:
            print(f"no project named {name!r} under {root}", file=sys.stderr)
            failures += 1
            continue
        failures += 0 if _launch_one(project, editor=editor, wait=wait, force=force) == 0 else 1
    return 1 if failures else 0


def _launch_one(project: Project, *, editor: bool, wait: bool, force: bool, restart: bool = False) -> int:
    print(f"→ {project.name}")
    checks = preflight.check_project(project, check_ports=not restart)
    blockers = preflight.blocking(checks)

    for check in checks:
        if check.level != "ok":
            print(f"  {check.render()}")

    if blockers and not restart:
        if not force:
            # stdout carries the findings; flush so they land above the verdict.
            sys.stdout.flush()
            print("  ✗ pre-flight failed — fix the above, or re-run with --force", file=sys.stderr)
            return 1
        if not _force_free_ports(project):
            sys.stdout.flush()
            print("  ✗ could not free every port — aborting", file=sys.stderr)
            return 1

    try:
        if editor:
            runner.open_editor(project)
            print(f"  ✓ editor {project.path}")
        launch = runner.restart_project if restart else runner.start_project
        for result in launch(project, wait_for_ports=wait):
            detail = f":{result.port}" if result.port else ""
            note = "" if result.port is None else (" ready" if result.port_ready else " (port not up yet)")
            print(f"  ✓ {result.task:<12} pid {result.pid:<7}{detail}{note}")
            print(f"    log {result.log_path}")
    except runner.LaunchError as exc:
        sys.stdout.flush()
        print(f"  ✗ {exc}", file=sys.stderr)
        return 1
    return 0


def _force_free_ports(project: Project) -> bool:
    """Free every port this project needs. False if any could not be freed."""
    freed_all = True
    for task in project.tasks:
        if task.port is None or not preflight.is_listening(task.port):
            continue
        owner = runner.free_port(task.port)
        if owner is not None:
            print(f"  ■ freed port {task.port} — killed {owner.render()}")
        else:
            freed_all = False
            print(
                f"  ✗ port {task.port} is busy but its owner cannot be identified "
                "(needs root on macOS) — stop it manually"
            )
    return freed_all


def cmd_restart(root: Path, name: str, *, wait: bool) -> int:
    project = find(root, name)
    if project is None:
        print(f"no project named {name!r} under {root}", file=sys.stderr)
        return 1
    return _launch_one(project, editor=False, wait=wait, force=False, restart=True)


def cmd_stop(name: str | None) -> int:
    targets = [name] if name else sorted({task.project for task in state.prune()})
    if not targets:
        print("nothing running")
        return 0
    for target in targets:
        stopped = runner.stop_project(target)
        print(f"■ {target}: {', '.join(stopped) if stopped else 'nothing running'}")
    return 0


def cmd_ps(*, as_json: bool = False) -> int:
    tasks = state.prune()
    if as_json:
        print(json.dumps([_task_payload(task) for task in tasks], indent=2))
        return 0
    if not tasks:
        print("nothing running")
        return 0
    for task in tasks:
        port = f":{task.port}" if task.port else ""
        print(f"{RUNNING_MARK} {task.project}/{task.task:<12} pid {task.pid:<7}{port:<7} {task.cmd}")
    return 0


def _task_payload(task: state.RunningTask) -> dict[str, object]:
    return {
        "project": task.project,
        "task": task.task,
        "pid": task.pid,
        "port": task.port,
        "cmd": task.cmd,
        "log": task.log_path,
    }


def cmd_doctor(root: Path, name: str | None) -> int:
    projects = load_projects(root) if name is None else [p for p in [find(root, name)] if p is not None]
    if not projects:
        print(f"no project named {name!r} under {root}", file=sys.stderr)
        return 1

    audit_all = name is None
    # One socket enumeration for the whole audit, not one per project.
    listeners = preflight.listening_ports()
    problems = 0
    for project in projects:
        # A library or CLI with nothing to serve is not broken, so a workspace-wide
        # audit skips it. Asking about it by name still explains the situation.
        if audit_all and not project.tasks:
            continue

        checks = preflight.check_project(project, listeners=listeners)
        noteworthy = [check for check in checks if check.level != "ok"]
        if audit_all and not noteworthy:
            continue
        print(f"\n{project.name} ({project.stack})")
        for check in noteworthy if audit_all else checks:
            print(f"  {check.render()}")
        problems += len(preflight.blocking(checks))

    if problems:
        print(f"\n{problems} blocking issue(s)")
        return 1
    print("\nno blocking issues")
    return 0


def cmd_new(
    root: Path,
    name: str | None,
    stack: str | None,
    *,
    dry_run: bool,
    git: bool,
    github: str,
) -> int:
    if stack is None or name is None:
        return _list_stacks(stack, name)

    try:
        recipe = scaffold.find_recipe(stack)
        plan = scaffold.build_plan(recipe, name, root, git=git, github=github)
    except scaffold.ScaffoldError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    absent = scaffold.missing_tools(recipe)
    if absent and not dry_run:
        print(f"✗ {recipe.title} needs {', '.join(absent)} on PATH", file=sys.stderr)
        return 1

    if dry_run:
        print("\n".join(scaffold.describe(plan)))
        return 0

    print(f"→ creating {plan.name} ({recipe.title})")
    try:
        target = scaffold.execute(plan)
    except scaffold.ScaffoldError as exc:
        sys.stdout.flush()
        print(f"✗ {exc}", file=sys.stderr)
        # Deliberately not deleted: a half-built project may hold work worth
        # keeping, and removing a user's directory is not ours to decide.
        print(f"  partial project left at {plan.target}", file=sys.stderr)
        return 1

    print(f"\n✓ {target}")
    print(f"  launch up {plan.name}      # editor + every service, in one keystroke")
    print(f"  launch doctor {plan.name}  # check prerequisites first")
    return 0


def _list_stacks(stack: str | None, name: str | None) -> int:
    incomplete = stack is not None or name is not None
    if stack is not None:
        print(f"missing project name: launch new <name> --stack {stack}", file=sys.stderr)
    elif name is not None:
        print(f"missing stack: launch new {name} --stack <stack>", file=sys.stderr)
    print("available stacks:" if not incomplete else "\navailable stacks:")
    for recipe in scaffold.all_recipes():
        absent = scaffold.missing_tools(recipe)
        status = f"  (needs {', '.join(absent)})" if absent else ""
        print(f"  {recipe.key:<9} {recipe.title:<22} {recipe.summary}{status}")
    if not incomplete:
        print("\nlaunch new <name> --stack <stack>")
    # Browsing the catalogue succeeded; only a half-specified command is an error.
    return 1 if incomplete else 0


def cmd_logs(name: str, *, follow: bool, lines: int) -> int:
    tracked = [task for task in state.load() if task.project == name]
    if not tracked:
        print(f"no logs recorded for {name}", file=sys.stderr)
        return 1

    for task in tracked:
        path = Path(task.log_path)
        print(f"--- {task.task}: {path}")
        for line in logs.tail(path, lines):
            print(line)

    return _follow(tracked) if follow else 0


def _follow(tracked: list[state.RunningTask]) -> int:
    followers = {task.task: logs.LogFollower(Path(task.log_path), from_end=True) for task in tracked}
    single = len(followers) == 1
    print("\n(following — ctrl-c to stop)")
    try:
        while True:
            for task_name, follower in followers.items():
                for line in follower.poll():
                    print(line if single else f"[{task_name}] {line}")
            time.sleep(FOLLOW_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return 0
