"""Dependency-aware concurrent execution of sessions, windows and panes.

Readiness rules:
  * A session is ready when all its windows are ready.
  * A window is ready when all its panes are ready.
  * A pane is ready once its readycheck succeeds.
  * A pane without a readycheck is ready as soon as its command is sent.
"""

import threading
from dataclasses import dataclass

from .errors import TmuxComposeError


@dataclass
class ReadyCheck:
    test: str = ""
    interval: float = 0.0  # seconds
    retries: int = 0


class Runnable:
    """Base for Pane/Window/Session: named, dependency-ordered, ready-signaling."""

    def __init__(self):
        self.name = ""
        self.readycheck = ReadyCheck()
        self.depends_on: list[str] = []
        self._ready = threading.Event()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def wait_until_ready(self) -> None:
        self._ready.wait()

    def mark_ready(self) -> None:
        self._ready.set()

    def run(self) -> None:
        """Perform this object's action. Overridden by subclasses."""

    def do_ready_check(self) -> None:
        """Block until this object counts as ready. Overridden by subclasses."""


def validate_dependencies(runners: list[Runnable]) -> dict[str, Runnable]:
    """Check names are unique and depends_on targets exist; return the index."""
    by_name: dict[str, Runnable] = {}
    for runnable in runners:
        if not runnable.name:
            continue
        if runnable.name in by_name:
            raise TmuxComposeError(f"Duplicate name: {runnable.name!r}")
        by_name[runnable.name] = runnable

    for runnable in runners:
        for dep in runnable.depends_on:
            if dep not in by_name:
                raise TmuxComposeError(f"Dependency does not exist: {dep}")
    return by_name


def run_all(runners: list[Runnable]) -> None:
    """Run every runner in its own thread, honoring depends_on ordering."""
    by_name = validate_dependencies(runners)
    errors: list[Exception] = []

    def worker(runnable: Runnable) -> None:
        try:
            for name in runnable.depends_on:
                by_name[name].wait_until_ready()
            runnable.run()
            runnable.do_ready_check()
        except Exception as err:
            errors.append(err)
        finally:
            # Signal readiness even on failure so dependents are never left
            # waiting forever; the error itself is re-raised after the join.
            runnable.mark_ready()

    threads = [threading.Thread(target=worker, args=(r,)) for r in runners]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors:
        raise errors[0]
