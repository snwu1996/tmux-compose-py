import pytest

from tmux_compose import runner, tmux
from tmux_compose.model import Project


@pytest.fixture(autouse=True)
def reset_registry():
    runner.all_runners.clear()
    runner.by_name.clear()
    tmux.restart = False
    yield
    runner.all_runners.clear()
    runner.by_name.clear()
    tmux.restart = False


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_run(fmt, *args):
        calls.append(fmt % args if args else fmt)
        return 0

    monkeypatch.setattr(tmux, "run", fake_run)
    return calls


def test_up_emits_expected_commands(captured):
    project = Project({
        "sessions": [{
            "name": "demo",
            "windows": [{
                "name": "code",
                "layout": "main-vertical",
                "focus": True,
                "panes": [{"cmd": "vim"}, {"cmd": "top"}],
            }],
        }],
    })
    project.up()

    joined = "\n".join(captured)
    assert 'tmux new-session -d -s demo -n "code" -c .' in captured
    assert "tmux split-window -t demo:0 -c ." in captured
    assert "tmux select-layout -t demo:0 main-vertical" in captured
    assert "tmux select-window -t demo:0" in captured
    # Both pane commands were sent.
    assert "tmux send-keys -t demo:0.0 'vim'" in joined
    assert "tmux send-keys -t demo:0.1 'top'" in joined

    # Spawn phase happens before the split for pane 1.
    assert captured.index('tmux new-session -d -s demo -n "code" -c .') < \
        captured.index("tmux split-window -t demo:0 -c .")


def test_dependency_ordering(captured):
    # pane "consumer" depends on window "db"; db's pane must run first.
    project = Project({
        "sessions": [{
            "name": "s",
            "windows": [
                {"name": "db", "panes": [{"cmd": "start-db"}]},
                {"panes": [{"cmd": "app", "depends_on": ["db"]}]},
            ],
        }],
    })
    project.up()

    joined = "\n".join(captured)
    db_idx = joined.index("start-db")
    app_idx = joined.index("'app'")
    assert db_idx < app_idx


def test_down_kills_sessions(captured):
    project = Project({
        "down_pre_cmd": "echo pre",
        "down_post_cmd": "echo post",
        "sessions": [{"name": "a"}, {"name": "b"}],
    })
    project.down()

    assert "cd .;echo pre" in captured
    assert "tmux kill-session -t a" in captured
    assert "tmux kill-session -t b" in captured
    assert "cd .;echo post" in captured
