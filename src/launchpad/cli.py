"""`launch` entry point — TUI by default, subcommands for scripting."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from launchpad import runner, state
from launchpad.config import Project
from launchpad.discovery import config_hint, discover

DEFAULT_ROOT_ENV = "LAUNCHPAD_ROOT"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()

    if not root.is_dir():
        print(f"root does not exist: {root}", file=sys.stderr)
        return 2

    handlers = {
        None: _cmd_tui,
        "ls": _cmd_ls,
        "up": _cmd_up,
        "stop": _cmd_stop,
        "ps": _cmd_ps,
        "logs": _cmd_logs,
    }
    return handlers[args.command](args, root)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="launch", description="Project launcher: repo -> running dev environment.")
    parser.add_argument(
        "--root",
        default=os.environ.get(DEFAULT_ROOT_ENV, str(Path.home() / "Desktop" / "experiments")),
        help="directory to scan for git repos (env: LAUNCHPAD_ROOT)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("ls", help="list discovered projects and detected tasks")
    sub.add_parser("ps", help="list processes launchpad is tracking")

    up = sub.add_parser("up", help="launch a project by name")
    up.add_argument("name")
    up.add_argument("--no-editor", action="store_true", help="start processes only")
    up.add_argument("--no-wait", action="store_true", help="do not wait for ports to open")

    stop = sub.add_parser("stop", help="stop a project's processes")
    stop.add_argument("name", nargs="?", help="omit to stop everything")

    logs = sub.add_parser("logs", help="print the log file path for a project's tasks")
    logs.add_argument("name")
    return parser


def _cmd_tui(_args: argparse.Namespace, root: Path) -> int:
    from launchpad.tui import run_tui  # imported lazily so subcommands stay fast

    run_tui(root)
    return 0


def _cmd_ls(_args: argparse.Namespace, root: Path) -> int:
    projects = discover(root)
    running = {task.project for task in state.prune()}
    for project in projects:
        mark = "●" if project.name in running else "○"
        print(f"{mark} {project.name:<28} {project.stack:<5} {project.summary}")
    print(f"\n{len(projects)} projects under {root}")
    return 0


def _cmd_up(args: argparse.Namespace, root: Path) -> int:
    project = _find(root, args.name)
    if project is None:
        print(f"no project named {args.name!r} under {root}", file=sys.stderr)
        return 1
    if not project.tasks:
        print(config_hint(project), file=sys.stderr)
        return 1

    print(f"→ {project.name}")
    try:
        if not args.no_editor:
            runner.open_editor(project)
            print(f"  ✓ editor {project.path}")
        for result in runner.start_project(project, wait_for_ports=not args.no_wait):
            detail = f":{result.port}" if result.port else ""
            note = "" if result.port is None else (" ready" if result.port_ready else " (port not up yet)")
            print(f"  ✓ {result.task} pid {result.pid} {detail}{note}")
            print(f"    log {result.log_path}")
    except runner.LaunchError as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_stop(args: argparse.Namespace, _root: Path) -> int:
    targets = [args.name] if args.name else sorted({task.project for task in state.prune()})
    if not targets:
        print("nothing running")
        return 0
    for name in targets:
        stopped = runner.stop_project(name)
        print(f"■ {name}: {', '.join(stopped) if stopped else 'nothing running'}")
    return 0


def _cmd_ps(_args: argparse.Namespace, _root: Path) -> int:
    tasks = state.prune()
    if not tasks:
        print("nothing running")
        return 0
    for task in tasks:
        port = f":{task.port}" if task.port else ""
        print(f"● {task.project}/{task.task:<10} pid {task.pid:<7}{port:<7} {task.cmd}")
    return 0


def _cmd_logs(args: argparse.Namespace, _root: Path) -> int:
    tasks = [task for task in state.load() if task.project == args.name]
    if not tasks:
        print(f"no logs recorded for {args.name}", file=sys.stderr)
        return 1
    for task in tasks:
        print(f"{task.task}: {task.log_path}")
    return 0


def _find(root: Path, name: str) -> Project | None:
    projects = discover(root)
    exact = next((p for p in projects if p.name.lower() == name.lower()), None)
    if exact is not None:
        return exact
    matches = [p for p in projects if name.lower() in p.name.lower()]
    return matches[0] if len(matches) == 1 else None


if __name__ == "__main__":
    raise SystemExit(main())
