"""Data model, strict YAML loading and project actions.

Faithful port of the structs and ``Project`` methods from the Go ``main.go``.
"""

import re

from . import tmux
from .runner import Object, ReadyCheck, add_runner, run_all

# --- Go-style duration parsing -------------------------------------------------

_UNITS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,  # µs
    "μs": 1e-6,  # μs (Greek mu, also accepted by Go)
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
        tmux.fatal("time: invalid duration %r" % str(value))
    return sign * total


# --- Strict loading helpers ----------------------------------------------------


def _ensure_mapping(data, context: str) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        tmux.fatal("Expected a mapping for %s" % context)
    return data


def _check_keys(data: dict, allowed: set, context: str) -> None:
    for key in data:
        if key not in allowed:
            tmux.fatal("Unknown field %r in %s" % (key, context))


def _load_readycheck(data, obj: Object) -> None:
    rc = _ensure_mapping(data, "readycheck")
    _check_keys(rc, {"test", "interval", "retries"}, "readycheck")
    obj.readycheck = ReadyCheck(
        test=rc.get("test", "") or "",
        interval=parse_duration(rc.get("interval")),
        retries=int(rc.get("retries", 0) or 0),
    )


def _load_object_fields(data: dict, obj: Object) -> None:
    """Populate the inlined Object fields (name/readycheck/depends_on)."""
    obj.name = data.get("name", "") or ""
    if "readycheck" in data:
        _load_readycheck(data.get("readycheck"), obj)
    depends = data.get("depends_on") or []
    obj.depends_on = list(depends)


# --- Model classes -------------------------------------------------------------


class Pane(Object):
    _ALLOWED = {"name", "readycheck", "depends_on", "dir", "focus", "cmd", "kill_cmd"}

    def __init__(self, data: dict):
        super().__init__()
        _check_keys(data, self._ALLOWED, "pane")
        _load_object_fields(data, self)
        self.dir = data.get("dir", "") or ""
        self.focus = bool(data.get("focus", False))
        self.cmd = data.get("cmd", "") or ""
        self.kill_cmd = data.get("kill_cmd", "") or ""
        self.target = ""

    def run(self) -> None:
        if tmux.restart and self.kill_cmd == "":
            return
        tmux.send_line(self.target, self.cmd)

    def do_ready_check(self) -> None:
        if self.readycheck.test == "":
            return
        while True:
            if tmux.run(self.readycheck.test) == 0:
                break
            if self.readycheck.retries <= 0:
                tmux.fatal("Object test failed?!")
            else:
                self.readycheck.retries -= 1
                _sleep(self.readycheck.interval)


class Window(Object):
    _ALLOWED = {"name", "readycheck", "depends_on", "dir", "focus", "layout", "panes"}

    def __init__(self, data: dict):
        super().__init__()
        _check_keys(data, self._ALLOWED, "window")
        _load_object_fields(data, self)
        self.dir = data.get("dir", "") or ""
        self.focus = bool(data.get("focus", False))
        self.layout = data.get("layout", "") or ""
        self.panes = [None if p is None else Pane(_ensure_mapping(p, "pane"))
                      for p in (data.get("panes") or [])]

    def do_ready_check(self) -> None:
        while True:
            ready = True
            for p in self.panes:
                if p is None:
                    continue
                if not p.is_ready():
                    ready = False
                    _sleep(0.100)
                    break
            if ready:
                return


class Session(Object):
    _ALLOWED = {"name", "readycheck", "depends_on", "dir", "windows"}

    def __init__(self, data: dict):
        super().__init__()
        _check_keys(data, self._ALLOWED, "session")
        _load_object_fields(data, self)
        self.dir = data.get("dir", "") or ""
        self.windows = [None if w is None else Window(_ensure_mapping(w, "window"))
                        for w in (data.get("windows") or [])]
        self.started = False

    def do_ready_check(self) -> None:
        while True:
            ready = True
            for w in self.windows:
                if w is None:
                    continue
                if not w.is_ready():
                    ready = False
                    _sleep(0.100)
                    break
            if ready:
                return


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

    def get_dir(self, s: Session, w: Window, pane_index: int) -> str:
        if pane_index > len(w.panes):
            tmux.fatal("Pane index out of bounds?!")

        if pane_index == 0 and len(w.panes) == 0:
            # The window has no explicit panes.
            return tmux.coalesce(w.dir, s.dir, self.dir, ".")

        return tmux.coalesce(w.panes[pane_index].dir, w.dir, s.dir, self.dir, ".")

    def up(self) -> None:
        if self.up_pre_cmd != "":
            tmux.shell_in_dir(self.dir, self.up_pre_cmd)

        # Spawn all the sessions/windows/panes.
        for s in self.sessions:
            for wi, w in enumerate(s.windows):
                if w is None:
                    continue
                target = "%s:%d" % (s.name, wi)
                dir = self.get_dir(s, w, 0)

                tmux.new_window(s, w, dir)

                for pi, p in enumerate(w.panes):
                    if p is None:
                        continue
                    p.target = "%s:%d.%d" % (s.name, wi, pi)
                    dir = self.get_dir(s, w, pi)
                    if pi > 0:
                        tmux.new_pane(target, dir)

                tmux.select_layout(target, w.layout)

        # Set which window has focus.
        for s in self.sessions:
            for wi, w in enumerate(s.windows):
                if w is None:
                    continue
                if w.focus:
                    target = "%s:%d" % (s.name, wi)
                    tmux.select_window(target)

        # Run the commands concurrently.
        for s in self.sessions:
            if s is None:
                continue
            add_runner(s)
            for w in s.windows:
                if w is None:
                    continue
                add_runner(w)
                for p in w.panes:
                    if p is None:
                        continue
                    add_runner(p)
        run_all()

        if self.up_post_cmd != "":
            tmux.shell_in_dir(self.dir, self.up_post_cmd)

    def down(self) -> None:
        if self.down_pre_cmd != "":
            tmux.shell_in_dir(self.dir, self.down_pre_cmd)

        for s in self.sessions:
            tmux.kill_session(s.name)

        if self.down_post_cmd != "":
            tmux.shell_in_dir(self.dir, self.down_post_cmd)

    def restart(self) -> None:
        for s in self.sessions:
            for wi, w in enumerate(s.windows):
                if w is None:
                    continue
                for pi, p in enumerate(w.panes):
                    if p is None:
                        continue
                    p.target = "%s:%d.%d" % (s.name, wi, pi)

                    if p.kill_cmd != "":
                        tmux.send_line(p.target, p.kill_cmd)

        # Used in each pane's run() to know we are performing a restart.
        # Only commands with a kill_cmd will be restarted.
        tmux.restart = True

        # Run the commands concurrently.
        for s in self.sessions:
            if s is None:
                continue
            add_runner(s)
            for w in s.windows:
                if w is None:
                    continue
                add_runner(w)
                for p in w.panes:
                    if p is None:
                        continue
                    add_runner(p)
        run_all()


def _sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)
