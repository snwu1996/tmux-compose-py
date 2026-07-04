"""Runner registry, dependency validation and concurrent execution.

Faithful port of the original Go ``runner.go``.

Readiness rules:
  * A session is ready when all its windows are ready.
  * A window is ready when all its panes are ready.
  * A pane is ready when its readycheck succeeds.
  * A pane without a readycheck is always ready.
"""

import threading
import time

from . import tmux


class ReadyCheck:
    def __init__(self, test: str = "", interval: float = 0.0, retries: int = 0):
        self.test = test
        self.interval = interval  # seconds
        self.retries = retries


class Object:
    """Base for Pane/Window/Session, mirroring the inlined Go ``Object``."""

    def __init__(self):
        self.name: str = ""
        self.readycheck = ReadyCheck()
        self.depends_on: list[str] = []
        self._mutex = threading.Lock()
        self._ready = False

    def get_object(self) -> "Object":
        return self

    def dependencies_ready(self) -> bool:
        for name in self.depends_on:
            other = by_name[name]
            if not other.is_ready():
                return False
        return True

    def is_ready(self) -> bool:
        with self._mutex:
            return self._ready

    def mark_ready(self) -> None:
        with self._mutex:
            self._ready = True

    def validate(self) -> None:
        for name in self.depends_on:
            if name not in by_name:
                tmux.fatal("Dependency does not exist: %s" % name)

    # Overridden by subclasses.
    def run(self) -> None:  # noqa: D401
        pass

    def do_ready_check(self) -> None:
        pass


all_runners: list[Object] = []
by_name: dict[str, Object] = {}


def add_runner(r: Object) -> None:
    all_runners.append(r)

    name = r.get_object().name
    if name == "":
        return
    if name in by_name:
        tmux.fatal("Duplicate name: '%s'" % name)
    by_name[name] = r


def validate_dependencies() -> None:
    for r in all_runners:
        r.get_object().validate()


def run_all() -> None:
    validate_dependencies()

    threads: list[threading.Thread] = []

    def worker(r: Object) -> None:
        while not r.dependencies_ready():
            time.sleep(0.010)
        r.run()
        r.do_ready_check()
        r.mark_ready()

    for r in all_runners:
        t = threading.Thread(target=worker, args=(r,))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()
