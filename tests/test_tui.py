"""TUI tests: the terminal emulator widget and the session-browser app.

Run against a real temporary tmux server (libtmux pytest plugin), driving
the Textual app headless via its test pilot.
"""

import time

import pytest

from tmux_compose import tmux
from tmux_compose.terminal import TerminalEmulator
from tmux_compose.tui import VIEWER_PREFIX, TmuxComposeApp

from textual.app import App
from textual.widgets import Tree


@pytest.fixture(autouse=True)
def _use_test_server(server):
    original = tmux.server
    tmux.server = server
    yield
    tmux.server = original


async def wait_for(pilot, predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


class TerminalHarness(App):
    def __init__(self, argv):
        super().__init__()
        self.argv = argv

    def compose(self):
        yield TerminalEmulator(self.argv)


async def test_terminal_emulator_runs_and_accepts_input():
    app = TerminalHarness(["bash", "--norc", "-i"])
    async with app.run_test(size=(80, 24)) as pilot:
        terminal = app.query_one(TerminalEmulator)
        terminal.focus()
        assert await wait_for(pilot, lambda: "$" in terminal.screen_text)

        for char in "echo marker-$((40 + 2))":
            await pilot.press(char)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: "marker-42" in terminal.screen_text)


async def test_tree_lists_sessions_windows_and_panes(server):
    session = server.new_session(session_name="alpha")
    session.new_window(window_name="beta")

    app = TmuxComposeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        labels = []

        def walk(node):
            labels.append(str(node.label))
            for child in node.children:
                walk(child)

        walk(tree.root)
        assert "alpha" in labels
        assert any(label.endswith("beta") for label in labels)
        # Two windows, one pane each.
        pane_leaves = [n for n in _all_nodes(tree.root) if n.data]
        assert len(pane_leaves) == 2


def _all_nodes(node):
    yield node
    for child in node.children:
        yield from _all_nodes(child)


async def test_selecting_pane_shows_it_in_terminal(server):
    session = server.new_session(session_name="target")
    session.active_pane.send_keys("echo hello-from-pane", enter=True)

    app = TmuxComposeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        pane_node = next(n for n in _all_nodes(tree.root) if n.data)
        tree.select_node(pane_node)
        await pilot.pause()

        # A grouped viewer session was created for the hidden client.
        names = [s.session_name for s in server.sessions]
        assert any(name.startswith(VIEWER_PREFIX) for name in names)

        terminal = app.query_one(TerminalEmulator)
        assert await wait_for(
            pilot, lambda: "hello-from-pane" in terminal.screen_text)

    # The viewer session is cleaned up when the app exits.
    names = [s.session_name for s in server.sessions]
    assert not any(name.startswith(VIEWER_PREFIX) for name in names)
