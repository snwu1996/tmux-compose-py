"""Command-line entry point. Faithful port of Go ``main()``."""

import argparse
import os
import sys

import yaml

from . import tmux
from .model import Project


def get_default_shell() -> str:
    sh = os.environ.get("SHELL", "")
    if sh == "":
        return "/bin/sh -c"
    return sh + " -c"


def main(argv=None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "-f", dest="compose_file", default="tmux-compose.yml",
        help="Specify an alternate compose file",
    )
    parser.add_argument(
        "-shell", dest="shell", default=get_default_shell(),
        help="Specify an alternate shell path",
    )
    parser.add_argument("action", nargs="?", default="")
    args = parser.parse_args(argv)

    print("Using shell args: %s" % args.shell)
    tmux.shell_args = args.shell.split(" ")

    if args.action == "":
        print("Usage: ", sys.argv[0], "[OPTIONS] <up> | <down>")
        sys.exit(1)

    try:
        with open(args.compose_file) as f:
            data = yaml.safe_load(f)
    except OSError as err:
        tmux.fatal(err)

    project = Project(data if data is not None else {})

    action = args.action
    if action == "up":
        project.up()
    elif action == "down":
        project.down()
    elif action == "restart":
        project.restart()


if __name__ == "__main__":
    main()
