"""Integration tests against a real (temporary) tmux server.

The ``server`` fixture comes from libtmux's bundled pytest plugin and gives
each test an isolated tmux server on its own socket.
"""

import time

import pytest

from tmux_compose import tmux
from tmux_compose.model import Project


@pytest.fixture(autouse=True)
def _use_test_server(server):
    original = tmux.server
    tmux.server = server
    yield
    tmux.server = original


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_up_creates_session_windows_and_panes(server, tmp_path):
    log = tmp_path / "out.log"
    project = Project({
        "sessions": [{
            "name": "demo",
            "windows": [{
                "name": "code",
                "layout": "main-vertical",
                "focus": True,
                "panes": [
                    {"cmd": f"echo one >> {log}"},
                    {"cmd": f"echo two >> {log}"},
                    None,  # empty "-" entry: a pane with no command
                ],
            }],
        }],
    })
    project.up()

    session = server.sessions.get(session_name="demo")
    assert len(session.windows) == 1
    window = session.windows[0]
    assert window.window_name == "code"
    assert len(window.panes) == 3

    # Both pane commands were sent and executed.
    assert wait_for(lambda: log.exists() and
                    sorted(log.read_text().split()) == ["one", "two"])


def test_dependency_ordering(server, tmp_path):
    # pane "consumer" depends on window "db"; db's pane must run first.
    log = tmp_path / "order.log"
    db_ready = tmp_path / "db-ready"
    project = Project({
        "sessions": [{
            "name": "s",
            "windows": [
                {"name": "db", "panes": [{
                    "cmd": f"echo db >> {log}; touch {db_ready}",
                    "readycheck": {
                        "test": f"test -f {db_ready}",
                        "interval": "200ms",
                        "retries": 50,
                    },
                }]},
                {"panes": [{"cmd": f"echo app >> {log}",
                            "depends_on": ["db"]}]},
            ],
        }],
    })
    project.up()

    assert wait_for(lambda: log.exists() and "app" in log.read_text())
    lines = log.read_text().split()
    assert lines.index("db") < lines.index("app")


def test_down_kills_sessions(server):
    server.new_session(session_name="a")
    server.new_session(session_name="b")

    project = Project({
        "sessions": [{"name": "a"}, {"name": "b"}, {"name": "never-existed"}],
    })
    project.down()  # killing a missing session must not raise

    names = [s.session_name for s in server.sessions]
    assert "a" not in names
    assert "b" not in names
