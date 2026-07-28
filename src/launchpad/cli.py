"""`launch` entry point — TUI by default, subcommands for scripting."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from launchpad import commands

DEFAULT_ROOT_ENV = "LAUNCHPAD_ROOT"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()

    if not root.is_dir():
        print(f"root does not exist: {root}", file=sys.stderr)
        return 2

    return _dispatch(args, root)


def _dispatch(args: argparse.Namespace, root: Path) -> int:
    if args.command is None:
        from launchpad.tui import run_tui  # imported lazily so subcommands stay fast

        run_tui(root)
        return 0
    if args.command == "ls":
        return commands.cmd_ls(root, as_json=args.json, with_git=args.git)
    if args.command == "up":
        return commands.cmd_up(
            root, args.names, editor=not args.no_editor, wait=not args.no_wait, force=args.force
        )
    if args.command == "restart":
        return commands.cmd_restart(root, args.name, wait=not args.no_wait)
    if args.command == "stop":
        return commands.cmd_stop(args.name)
    if args.command == "ps":
        return commands.cmd_ps(as_json=args.json)
    if args.command == "doctor":
        return commands.cmd_doctor(root, args.name)
    if args.command == "logs":
        return commands.cmd_logs(args.name, follow=args.follow, lines=args.lines)
    if args.command == "new":
        return commands.cmd_new(
            root,
            args.name,
            args.stack,
            dry_run=args.dry_run,
            git=not args.no_git,
            github=args.github,
        )
    raise AssertionError(f"unhandled command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="launch",
        description="Project launcher: pick a repo, get its whole dev environment running.",
    )
    parser.add_argument(
        "--root",
        default=os.environ.get(DEFAULT_ROOT_ENV, str(Path.home() / "Desktop" / "experiments")),
        help="directory to scan for git repos (env: LAUNCHPAD_ROOT)",
    )
    sub = parser.add_subparsers(dest="command")

    listing = sub.add_parser("ls", help="list projects, stacks, tasks, and run state")
    listing.add_argument("--json", action="store_true", help="machine-readable output")
    listing.add_argument("--git", action="store_true", help="include branch and dirty-file counts")

    up = sub.add_parser("up", help="launch one or more projects")
    up.add_argument("names", nargs="+")
    up.add_argument("--no-editor", action="store_true", help="start processes only")
    up.add_argument("--no-wait", action="store_true", help="do not wait for ports to open")
    up.add_argument("--force", action="store_true", help="kill whatever holds a needed port, then launch")

    restart = sub.add_parser("restart", help="stop then relaunch a project")
    restart.add_argument("name")
    restart.add_argument("--no-wait", action="store_true", help="do not wait for ports to open")

    stop = sub.add_parser("stop", help="stop a project's processes")
    stop.add_argument("name", nargs="?", help="omit to stop everything")

    processes = sub.add_parser("ps", help="list processes launchpad is tracking")
    processes.add_argument("--json", action="store_true", help="machine-readable output")

    doctor = sub.add_parser("doctor", help="diagnose why a project would fail to launch")
    doctor.add_argument("name", nargs="?", help="omit to audit every project")

    new = sub.add_parser("new", help="scaffold a new project from a stack template")
    new.add_argument("name", nargs="?", help="project name; omit to list available stacks")
    new.add_argument("--stack", help="next | mern | spring | fastapi")
    new.add_argument("--dry-run", action="store_true", help="print the plan without running it")
    new.add_argument("--no-git", action="store_true", help="skip git init and the first commit")
    new.add_argument(
        "--github",
        choices=("none", "public", "private"),
        default="none",
        help="also create and push a GitHub repo (default: none)",
    )

    log_view = sub.add_parser("logs", help="show a project's task logs")
    log_view.add_argument("name")
    log_view.add_argument("-f", "--follow", action="store_true", help="stream new output")
    log_view.add_argument("-n", "--lines", type=int, default=50, help="lines of history per task")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
