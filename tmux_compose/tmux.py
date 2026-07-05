"""Local shell helpers and libtmux-backed tmux operations.

All tmux interaction goes through libtmux: sessions, windows and panes are
created and driven via :class:`libtmux.Server` and the objects it returns.
The local-shell helpers (``run``/``shell``) remain for readychecks and
pre/post commands.
"""

import subprocess

import libtmux
from libtmux.exc import LibTmuxException

from .errors import TmuxComposeError

VALID_LAYOUTS = frozenset({
    "even-horizontal",
    "even-vertical",
    "main-horizontal",
    "main-vertical",
    "titled",
})

# Split shell invocation, e.g. ["/bin/sh", "-c"]. Set by the CLI at startup.
shell_args: list[str] = ["/bin/sh", "-c"]

# The tmux server all operations go through. Tests replace this with a
# temporary server from libtmux's pytest plugin.
server = libtmux.Server()


def run(cmd: str) -> int:
    """Run a command through the configured shell, echoing it first.

    Captures combined stdout+stderr and returns the exit code (0 on success).
    """
    print(cmd)
    proc = subprocess.run(
        [*shell_args, cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode


def shell(cmd: str) -> None:
    """Run a command and raise if it fails."""
    if run(cmd) != 0:
        raise TmuxComposeError(f"command failed: {cmd}")


def shell_in_dir(directory: str, cmd: str) -> None:
    shell(f"cd {directory or '.'};{cmd}")


def new_window(session, window, directory: str) -> libtmux.Window:
    """Create a window for ``session``, creating the session on first use."""
    name = window.name or None
    try:
        if session.started:
            return session.tmux_session.new_window(
                window_name=name, start_directory=directory, attach=False)
        session.tmux_session = server.new_session(
            session_name=session.name, window_name=name,
            start_directory=directory, attach=False)
    except LibTmuxException as err:
        raise TmuxComposeError(err) from err
    session.started = True
    return session.tmux_session.active_window


def new_pane(window: libtmux.Window, directory: str) -> libtmux.Pane:
    try:
        return window.split(start_directory=directory, attach=True)
    except LibTmuxException as err:
        raise TmuxComposeError(err) from err


def select_window(window: libtmux.Window) -> None:
    try:
        window.select()
    except LibTmuxException as err:
        raise TmuxComposeError(err) from err


def select_layout(window: libtmux.Window, layout: str) -> None:
    if not layout:
        return
    if layout not in VALID_LAYOUTS:
        raise TmuxComposeError(f"Bad layout: {layout}")
    try:
        window.select_layout(layout)
    except LibTmuxException as err:
        raise TmuxComposeError(err) from err


def send_line(pane: libtmux.Pane, text: str) -> None:
    if not text:
        return
    print(text)
    try:
        pane.send_keys(text, enter=True, suppress_history=False)
    except LibTmuxException as err:
        raise TmuxComposeError(err) from err


def find_pane(session_name: str, window_index: int, pane_index: int) -> libtmux.Pane:
    """Look up an existing pane by list position (not tmux index, so this
    works regardless of the user's base-index setting)."""
    session = server.sessions.get(session_name=session_name, default=None)
    if session is None:
        raise TmuxComposeError(f"Session does not exist: {session_name}")
    try:
        return session.windows[window_index].panes[pane_index]
    except IndexError:
        raise TmuxComposeError(
            f"Pane does not exist: {session_name}:{window_index}.{pane_index}"
        ) from None


def kill_session(session_name: str) -> None:
    # A missing session is not an error: "down" should be idempotent.
    try:
        server.kill_session(session_name)
    except LibTmuxException:
        pass


def set_environment(session_name: str, key: str, value: str) -> None:
    session = server.sessions.get(session_name=session_name, default=None)
    if session is None:
        raise TmuxComposeError(f"Session does not exist: {session_name}")
    try:
        session.set_environment(key, value)
    except LibTmuxException as err:
        raise TmuxComposeError(err) from err
