"""A terminal emulator widget for Textual.

Runs a command inside a pty, feeds its output through a pyte VT100 screen,
and renders that screen as widget content. Key presses are translated to
escape sequences and written back to the pty, so the child process is fully
interactive.
"""

import asyncio
import fcntl
import os
import pty
import struct
import subprocess
import termios
import traceback

import pyte
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

# pyte color names that Rich spells differently.
_COLOR_ALIASES = {
    "brown": "yellow",
    "brightblack": "bright_black",
    "brightred": "bright_red",
    "brightgreen": "bright_green",
    "brightbrown": "bright_yellow",
    "brightyellow": "bright_yellow",
    "brightblue": "bright_blue",
    "brightmagenta": "bright_magenta",
    "brightcyan": "bright_cyan",
    "brightwhite": "bright_white",
}

_HEX_DIGITS = set("0123456789abcdef")

# Set TMUX_COMPOSE_DEBUG=/path/to/file to capture the raw client byte stream
# (and any emulator parse errors) for diagnosing rendering issues.
_DEBUG_LOG = os.environ.get("TMUX_COMPOSE_DEBUG", "")

# Textual key name -> bytes written to the pty.
_KEY_BYTES = {
    "enter": b"\r",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "tab": b"\t",
    "shift+tab": b"\x1b[Z",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "insert": b"\x1b[2~",
    "delete": b"\x1b[3~",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
    "ctrl+space": b"\x00",
}


def _rich_color(value: str) -> str | None:
    """Translate a pyte color value to something Rich understands."""
    if value == "default":
        return None
    if value in _COLOR_ALIASES:
        return _COLOR_ALIASES[value]
    if len(value) == 6 and set(value) <= _HEX_DIGITS:
        return f"#{value}"
    return value


class TerminalEmulator(Widget, can_focus=True):
    """Runs ``argv`` in a pty and displays it as a live, typeable terminal."""

    DEFAULT_CSS = """
    TerminalEmulator {
        width: 1fr;
        height: 1fr;
    }
    TerminalEmulator:focus {
        border: none;
    }
    """

    def __init__(self, argv: list[str], **kwargs):
        super().__init__(**kwargs)
        self.argv = argv
        self._screen: pyte.Screen | None = None
        self._stream: pyte.ByteStream | None = None
        self._master_fd: int | None = None
        self._process: subprocess.Popen | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.exited = False

    # --- process lifecycle -------------------------------------------------

    def _spawn(self, cols: int, rows: int) -> None:
        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.ByteStream(self._screen)

        self._master_fd, slave_fd = pty.openpty()
        self._set_winsize(cols, rows)

        env = os.environ.copy()
        env.pop("TMUX", None)  # allow attaching to tmux from inside tmux
        env["TERM"] = "xterm-256color"

        def _become_tty_leader():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self._process = subprocess.Popen(
            self.argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            preexec_fn=_become_tty_leader,
        )
        os.close(slave_fd)

        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(self._master_fd, self._on_pty_readable)

    def _on_pty_readable(self) -> None:
        try:
            data = os.read(self._master_fd, 65536)
        except OSError:
            data = b""
        if not data:
            self._teardown()
            self.exited = True
            self.refresh()
            return
        if _DEBUG_LOG:
            with open(_DEBUG_LOG, "ab") as f:
                f.write(data)
        try:
            self._stream.feed(data)
        except Exception:
            # A parse error must never kill the read pipeline: dropping the
            # chunk may glitch the display, but the next redraw recovers it.
            if _DEBUG_LOG:
                with open(_DEBUG_LOG, "a") as f:
                    f.write("\n--- feed() error ---\n")
                    f.write(traceback.format_exc())
        self.refresh()

    def _set_winsize(self, cols: int, rows: int) -> None:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def _teardown(self) -> None:
        if self._master_fd is not None:
            self._loop.remove_reader(self._master_fd)
            os.close(self._master_fd)
            self._master_fd = None
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._process = None

    def stop(self) -> None:
        """Kill the child process and stop reading from the pty."""
        self._teardown()

    def on_unmount(self) -> None:
        self._teardown()

    # --- geometry ----------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        cols = max(2, event.size.width)
        rows = max(2, event.size.height)
        if self._process is None and not self.exited:
            self._spawn(cols, rows)
        elif self._master_fd is not None:
            self._screen.resize(rows, cols)
            self._set_winsize(cols, rows)
            self.refresh()

    # --- rendering ---------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        if self._screen is None or y >= self._screen.lines:
            return Strip.blank(self.size.width)

        buffer_line = self._screen.buffer[y]
        cursor = self._screen.cursor
        show_cursor = (self.has_focus and not cursor.hidden
                       and self._process is not None and cursor.y == y)

        segments = []
        run_chars: list[str] = []
        run_style: Style | None = None
        for x in range(self._screen.columns):
            char = buffer_line[x]
            style = Style(
                color=_rich_color(char.fg),
                bgcolor=_rich_color(char.bg),
                bold=char.bold,
                italic=char.italics,
                underline=char.underscore,
                strike=char.strikethrough,
                reverse=char.reverse != (show_cursor and cursor.x == x),
            )
            if style != run_style and run_chars:
                segments.append(Segment("".join(run_chars), run_style))
                run_chars = []
            run_style = style
            run_chars.append(char.data)
        if run_chars:
            segments.append(Segment("".join(run_chars), run_style))

        return Strip(segments).extend_cell_length(self.size.width)

    @property
    def screen_text(self) -> str:
        """The emulated screen as plain text (used by tests)."""
        if self._screen is None:
            return ""
        return "\n".join(self._screen.display)

    # --- input -------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if self._master_fd is None:
            return
        data = _KEY_BYTES.get(event.key)
        if data is None and event.key.startswith("ctrl+") and len(event.key) == 6:
            letter = event.key[-1]
            if "a" <= letter <= "z":
                data = bytes([ord(letter) - ord("a") + 1])
        if data is None and event.character:
            data = event.character.encode()
        if data is None:
            return
        event.stop()
        event.prevent_default()
        os.write(self._master_fd, data)
