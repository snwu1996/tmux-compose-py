"""Exceptions for tmux-compose."""


class TmuxComposeError(Exception):
    """Raised for config, dependency and tmux/shell execution failures.

    The CLI catches this at the top level, prints the message to stderr and
    exits with status 1.
    """
