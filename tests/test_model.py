import pytest

from tmux_compose import runner
from tmux_compose.model import Pane, Project, Session, Window


@pytest.fixture(autouse=True)
def reset_registry():
    runner.all_runners.clear()
    runner.by_name.clear()
    yield
    runner.all_runners.clear()
    runner.by_name.clear()


# --- strict loading ------------------------------------------------------------


def test_unknown_project_key_exits():
    with pytest.raises(SystemExit):
        Project({"bogus": 1})


def test_unknown_pane_key_exits():
    with pytest.raises(SystemExit):
        Pane({"command": "vim"})  # should be "cmd"


def test_unknown_readycheck_key_exits():
    with pytest.raises(SystemExit):
        Pane({"cmd": "x", "readycheck": {"tset": "y"}})


def test_valid_project_loads():
    project = Project({
        "dir": "/proj",
        "sessions": [
            {"name": "s1", "windows": [
                {"name": "w1", "layout": "main-vertical", "focus": True,
                 "panes": [{"cmd": "top", "kill_cmd": "C-c"}, None]},
            ]},
        ],
    })
    assert project.dir == "/proj"
    assert project.sessions[0].name == "s1"
    w = project.sessions[0].windows[0]
    assert w.layout == "main-vertical" and w.focus is True
    assert w.panes[0].cmd == "top" and w.panes[0].kill_cmd == "C-c"
    assert w.panes[1] is None


def test_readycheck_interval_parsed():
    pane = Pane({"cmd": "x", "readycheck": {
        "test": "true", "interval": "3s", "retries": 2}})
    assert pane.readycheck.test == "true"
    assert pane.readycheck.interval == 3.0
    assert pane.readycheck.retries == 2


# --- get_dir coalescing --------------------------------------------------------


def _project_with(pane_dir="", window_dir="", session_dir="", project_dir=""):
    project = Project({
        "dir": project_dir,
        "sessions": [{
            "dir": session_dir,
            "windows": [{
                "dir": window_dir,
                "panes": [{"cmd": "x", "dir": pane_dir}],
            }],
        }],
    })
    s = project.sessions[0]
    w = s.windows[0]
    return project, s, w


def test_get_dir_prefers_pane():
    project, s, w = _project_with(pane_dir="pd", window_dir="wd",
                                  session_dir="sd", project_dir="prd")
    assert project.get_dir(s, w, 0) == "pd"


def test_get_dir_falls_through_to_window():
    project, s, w = _project_with(window_dir="wd", session_dir="sd",
                                  project_dir="prd")
    assert project.get_dir(s, w, 0) == "wd"


def test_get_dir_falls_through_to_project():
    project, s, w = _project_with(project_dir="prd")
    assert project.get_dir(s, w, 0) == "prd"


def test_get_dir_defaults_to_dot():
    project, s, w = _project_with()
    assert project.get_dir(s, w, 0) == "."


def test_get_dir_no_panes_uses_window():
    project = Project({"sessions": [{"windows": [{"dir": "wd"}]}]})
    s = project.sessions[0]
    w = s.windows[0]
    assert project.get_dir(s, w, 0) == "wd"


# --- dependency validation -----------------------------------------------------


def test_missing_dependency_exits():
    p = Pane({"cmd": "x", "depends_on": ["nope"]})
    runner.add_runner(p)
    with pytest.raises(SystemExit):
        runner.validate_dependencies()


def test_present_dependency_ok():
    dep = Window({"name": "db", "panes": [{"cmd": "start"}]})
    consumer = Pane({"cmd": "run", "depends_on": ["db"]})
    runner.add_runner(dep)
    runner.add_runner(consumer)
    runner.validate_dependencies()  # should not raise


def test_duplicate_name_exits():
    runner.add_runner(Session({"name": "dup"}))
    with pytest.raises(SystemExit):
        runner.add_runner(Session({"name": "dup"}))
