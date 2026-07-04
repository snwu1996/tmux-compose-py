"""Shell execution and tmux command wrappers.

Faithful port of the helpers in the original Go ``main.go``. Shared mutable
state (``gShellArgs``/``gRestart`` in the Go source) is kept here as module
globals so behavior matches the original exactly.
"""

import subprocess
import sys

# Split shell invocation, e.g. ["/bin/sh", "-c"]. Set by the CLI at startup.
shell_args: list[str] = ["/bin/sh", "-c"]

# Set to True during a restart so panes without a kill_cmd are skipped.
restart: bool = False


def fatal(message: object) -> None:
    """Mirror Go's log.Fatal: print to stderr and exit with status 1."""
    print(message, file=sys.stderr)
    sys.exit(1)


def run(fmt: str, *args: object) -> int:
    """Run a command string through the configured shell.

    Prints the command (as Go's ``fmt.Println(cmdStr)`` does), captures combined
    stdout+stderr, and returns the process return code (0 on success).
    """
    cmd_str = fmt % args if args else fmt
    print(cmd_str)
    proc = subprocess.run(
        [shell_args[0], *shell_args[1:], cmd_str],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode


def shell(fmt: str, *args: object) -> None:
    """Run a command and abort the program if it fails (Go's ``shell``)."""
    if run(fmt, *args) != 0:
        cmd_str = fmt % args if args else fmt
        fatal("command failed: " + cmd_str)


def shell_in_dir(dir: str, cmd: str) -> None:
    shell("cd %s;%s", coalesce(dir, "."), cmd)


def coalesce(*args: str) -> str:
    """Return the first non-empty string, else ``""``."""
    for s in args:
        if s:
            return s
    return ""


def new_window(session, window, dir: str) -> None:
    named_window = ""
    if window.name:
        named_window = '-n "%s"' % window.name
    if session.started:
        shell("tmux new-window -d -t %s %s -c %s", session.name, named_window, dir)
    else:
        shell("tmux new-session -d -s %s %s -c %s", session.name, named_window, dir)
        session.started = True


def new_pane(target: str, dir: str) -> None:
    shell("tmux split-window -t %s -c %s", target, dir)


def select_window(target: str) -> None:
    shell("tmux select-window -t %s", target)


def select_layout(target: str, layout: str) -> None:
    if layout == "":
        return
    if layout not in (
        "even-horizontal",
        "even-vertical",
        "main-horizontal",
        "main-vertical",
        "titled",
    ):
        fatal("Bad layout: " + layout)
    shell("tmux select-layout -t %s %s", target, layout)


def send_line(target: str, text: str) -> None:
    if text == "":
        return
    shell("tmux send-keys -t %s '%s'", target, text)
    shell("tmux send-keys -R -t %s 'Enter'", target)


def kill_session(session: str) -> None:
    # Errors are intentionally ignored (Go used ``run`` here, not ``shell``).
    run("tmux kill-session -t %s", session)


def set_environment(session: str, key: str, value: str) -> None:
    shell("tmux set-environment -t %s %s %s", session, key, value)
