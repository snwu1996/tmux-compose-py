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
    parser.add_argument("action", choices=["up", "down", "restart"])
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)

    print(f"Using shell args: {args.shell}")
    tmux.shell_args = args.shell.split()

    try:
        with open(args.compose_file) as f:
            data = yaml.safe_load(f)
        project = Project(data or {})
        getattr(project, args.action)()
    except (OSError, yaml.YAMLError, TmuxComposeError) as err:
        print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
