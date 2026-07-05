"""Data model, strict YAML loading and project actions."""

import re
import time

from . import tmux
from .errors import TmuxComposeError
from .runner import ReadyCheck, Runnable, run_all

# --- Go-style duration parsing -------------------------------------------------
# Durations in config files use Go's syntax (e.g. "3s", "1m30s") so existing
# compose files keep working.

_UNITS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,  # micro sign
    "μs": 1e-6,  # Greek mu, also accepted by Go
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}

_DUR_RE = re.compile(r"([0-9]*\.?[0-9]+)(ns|us|µs|μs|ms|s|m|h)")


def parse_duration(value) -> float:
    """Parse a Go duration string (e.g. ``"3s"``, ``"1m30s"``) into seconds.

    Ints/floats are treated as nanoseconds, matching how Go represents a bare
    ``time.Duration`` integer. ``0`` is valid and yields ``0``.
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) * 1e-9

    s = str(value).strip()
    if s in ("0", "+0", "-0"):
        return 0.0

    sign = 1.0
    if s and s[0] in "+-":
        if s[0] == "-":
            sign = -1.0
        s = s[1:]

    pos = 0
    total = 0.0
    matched = False
    for m in _DUR_RE.finditer(s):
        if m.start() != pos:
            break
        matched = True
        total += float(m.group(1)) * _UNITS[m.group(2)]
        pos = m.end()

    if not matched or pos != len(s):
        raise TmuxComposeError(f"invalid duration: {value!r}")
    return sign * total


# --- Strict loading helpers ----------------------------------------------------


def _ensure_mapping(data, context: str) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TmuxComposeError(f"Expected a mapping for {context}")
    return data


def _check_keys(data: dict, allowed: set, context: str) -> None:
    for key in data:
        if key not in allowed:
            raise TmuxComposeError(f"Unknown field {key!r} in {context}")


def _load_readycheck(data, obj: Runnable) -> None:
    rc = _ensure_mapping(data, "readycheck")
    _check_keys(rc, {"test", "interval", "retries"}, "readycheck")
    obj.readycheck = ReadyCheck(
        test=rc.get("test", "") or "",
        interval=parse_duration(rc.get("interval")),
        retries=int(rc.get("retries", 0) or 0),
    )


def _load_runnable_fields(data: dict, obj: Runnable) -> None:
    """Populate the shared Runnable fields (name/readycheck/depends_on)."""
    obj.name = data.get("name", "") or ""
    if "readycheck" in data:
        _load_readycheck(data.get("readycheck"), obj)
    obj.depends_on = list(data.get("depends_on") or [])


# --- Model classes -------------------------------------------------------------


class Pane(Runnable):
    _ALLOWED = {"name", "readycheck", "depends_on", "dir", "focus", "cmd", "kill_cmd"}

    def __init__(self, data: dict):
        super().__init__()
        _check_keys(data, self._ALLOWED, "pane")
        _load_runnable_fields(data, self)
        self.dir = data.get("dir", "") or ""
        self.focus = bool(data.get("focus", False))
        self.cmd = data.get("cmd", "") or ""
        self.kill_cmd = data.get("kill_cmd", "") or ""
        self.pane = None  # libtmux.Pane, set when the pane is spawned/found
        self.restarting = False  # only panes with a kill_cmd rerun on restart

    def run(self) -> None:
        if self.restarting and not self.kill_cmd:
            return
        tmux.send_line(self.pane, self.cmd)

    def do_ready_check(self) -> None:
        rc = self.readycheck
        if not rc.test:
            return
        retries = rc.retries
        while tmux.run(rc.test) != 0:
            if retries <= 0:
                raise TmuxComposeError(f"readycheck failed: {rc.test}")
            retries -= 1
            time.sleep(rc.interval)


class Window(Runnable):
    _ALLOWED = {"name", "readycheck", "depends_on", "dir", "focus", "layout", "panes"}

    def __init__(self, data: dict):
        super().__init__()
        _check_keys(data, self._ALLOWED, "window")
        _load_runnable_fields(data, self)
        self.dir = data.get("dir", "") or ""
        self.focus = bool(data.get("focus", False))
        self.layout = data.get("layout", "") or ""
        self.panes = [None if p is None else Pane(_ensure_mapping(p, "pane"))
                      for p in (data.get("panes") or [])]
        self.tmux_window = None  # libtmux.Window, set when the window is spawned

    def do_ready_check(self) -> None:
        for pane in self.panes:
            if pane is not None:
                pane.wait_until_ready()


class Session(Runnable):
    _ALLOWED = {"name", "readycheck", "depends_on", "dir", "windows"}

    def __init__(self, data: dict):
        super().__init__()
        _check_keys(data, self._ALLOWED, "session")
        _load_runnable_fields(data, self)
        self.dir = data.get("dir", "") or ""
        self.windows = [None if w is None else Window(_ensure_mapping(w, "window"))
                        for w in (data.get("windows") or [])]
        self.started = False
        self.tmux_session = None  # libtmux.Session, set on first window spawn

    def do_ready_check(self) -> None:
        for window in self.windows:
            if window is not None:
                window.wait_until_ready()


class Project:
    _ALLOWED = {
        "dir", "up_pre_cmd", "up_post_cmd", "down_pre_cmd", "down_post_cmd", "sessions",
    }

    def __init__(self, data: dict):
        _check_keys(data, self._ALLOWED, "project")
        self.dir = data.get("dir", "") or ""
        self.up_pre_cmd = data.get("up_pre_cmd", "") or ""
        self.up_post_cmd = data.get("up_post_cmd", "") or ""
        self.down_pre_cmd = data.get("down_pre_cmd", "") or ""
        self.down_post_cmd = data.get("down_post_cmd", "") or ""
        self.sessions = [None if s is None else Session(_ensure_mapping(s, "session"))
                         for s in (data.get("sessions") or [])]

    def get_dir(self, session: Session, window: Window, pane_index: int) -> str:
        pane_dir = ""
        if pane_index < len(window.panes) and window.panes[pane_index] is not None:
            pane_dir = window.panes[pane_index].dir
        return pane_dir or window.dir or session.dir or self.dir or "."

    def _runners(self) -> list[Runnable]:
        runners: list[Runnable] = []
        for session in self.sessions:
            if session is None:
                continue
            runners.append(session)
            for window in session.windows:
                if window is None:
                    continue
                runners.append(window)
                runners.extend(p for p in window.panes if p is not None)
        return runners

    def up(self) -> None:
        if self.up_pre_cmd:
            tmux.shell_in_dir(self.dir, self.up_pre_cmd)

        # Spawn all the sessions/windows/panes.
        for session in self.sessions:
            if session is None:
                continue
            for window in session.windows:
                if window is None:
                    continue
                window.tmux_window = tmux.new_window(
                    session, window, self.get_dir(session, window, 0))

                # A None entry (an empty "-" in the YAML) still creates a
                # pane; it just gets no command.
                for pane_index, pane in enumerate(window.panes):
                    directory = self.get_dir(session, window, pane_index)
                    if pane_index > 0:
                        tmux_pane = tmux.new_pane(window.tmux_window, directory)
                    else:
                        tmux_pane = window.tmux_window.active_pane
                    if pane is not None:
                        pane.pane = tmux_pane

                tmux.select_layout(window.tmux_window, window.layout)

        # Set which window has focus.
        for session in self.sessions:
            if session is None:
                continue
            for window in session.windows:
                if window is not None and window.focus:
                    tmux.select_window(window.tmux_window)

        # Run the commands concurrently.
        run_all(self._runners())

        if self.up_post_cmd:
            tmux.shell_in_dir(self.dir, self.up_post_cmd)

    def down(self) -> None:
        if self.down_pre_cmd:
            tmux.shell_in_dir(self.dir, self.down_pre_cmd)

        for session in self.sessions:
            if session is not None:
                tmux.kill_session(session.name)

        if self.down_post_cmd:
            tmux.shell_in_dir(self.dir, self.down_post_cmd)

    def restart(self) -> None:
        for session in self.sessions:
            if session is None:
                continue
            for window_index, window in enumerate(session.windows):
                if window is None:
                    continue
                for pane_index, pane in enumerate(window.panes):
                    if pane is None:
                        continue
                    pane.pane = tmux.find_pane(session.name, window_index, pane_index)
                    # Only panes with a kill_cmd are restarted.
                    pane.restarting = True
                    if pane.kill_cmd:
                        tmux.send_line(pane.pane, pane.kill_cmd)

        run_all(self._runners())
