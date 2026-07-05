"""Command-line entry point."""

import argparse
import os
import sys

import yaml

from . import tmux
from .errors import TmuxComposeError
from .model import Project


def default_shell() -> str:
    shell = os.environ.get("SHELL") or "/bin/sh"
    return f"{shell} -c"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tmux-compose")
    parser.add_argument(
        "-f", "--file", dest="compose_file", default="tmux-compose.yml",
        help="Specify an alternate compose file",
    )
    parser.add_argument(
        "-shell", "--shell", dest="shell", default=default_shell(),
        help="Specify an alternate shell invocation",
    )
    parser.add_argument(
        "--tui", action="store_true",
        help="After 'up', open a TUI that browses tmux sessions and views panes live",
    )
    parser.add_argument("action", choices=["up", "down", "restart", "tui"])
    args = parser.parse_args(argv)
    if args.tui and args.action != "up":
        parser.error("--tui can only be used with 'up' (use 'tmux-compose tui' to open the TUI standalone)")
    return args


def main(argv=None) -> None:
    args = parse_args(argv)

    print(f"Using shell args: {args.shell}")
    tmux.shell_args = args.shell.split()

    try:
        if args.action != "tui":
            with open(args.compose_file) as f:
                data = yaml.safe_load(f)
            project = Project(data or {})
            getattr(project, args.action)()
    except (OSError, yaml.YAMLError, TmuxComposeError) as err:
        print(err, file=sys.stderr)
        sys.exit(1)

    if args.action == "tui" or args.tui:
        from .tui import run_tui  # deferred: textual is slow to import
        run_tui()


if __name__ == "__main__":
    main()
