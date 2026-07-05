"""Session browser TUI: a tree of tmux sessions with a live pane viewer.

The left panel lists every session/window/pane on the server. Selecting a
pane attaches a hidden tmux client to it and shows it on the right inside a
:class:`~tmux_compose.terminal.TerminalEmulator`; selecting a window shows
all of its panes laid out as a normal attached client would.

Viewing uses a *grouped session* (``tmux new-session -t <target>``): it
shares the target's windows but has its own current window, so pointing the
viewer at a pane does not change which window the user's other tmux clients
are looking at.

The selected pane is zoomed (``resize-pane -Z``) so it fills the viewer even
when its window holds other panes. Zoom is a window-level flag shared by all
sessions in the group, so other clients viewing the same window see the zoom
too; it is undone when the selection moves elsewhere or the app exits.
"""

import os

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Static, Tree

from . import tmux
from .terminal import TerminalEmulator

VIEWER_PREFIX = "_tui-"


def _viewer_name() -> str:
    return f"{VIEWER_PREFIX}{os.getpid()}"


def _attach_argv(viewer: str) -> list[str]:
    """Build the tmux client command for the terminal widget, pointing it at
    the same server that ``tmux.server`` talks to."""
    argv = ["tmux"]
    if tmux.server.socket_path:
        argv += ["-S", str(tmux.server.socket_path)]
    elif tmux.server.socket_name:
        argv += ["-L", tmux.server.socket_name]
    return [*argv, "attach-session", "-t", viewer]


def _zoomed_on(pane_id: str) -> bool | None:
    """``None`` if the pane's window is not zoomed (or the pane is gone),
    ``True`` if it is zoomed on this pane, ``False`` if on a sibling. The
    zoomed pane is always its window's active pane."""
    out = tmux.server.cmd("display-message", "-p", "-t", pane_id,
                          "#{window_zoomed_flag} #{pane_active}").stdout
    if not out or not out[0].startswith("1"):
        return None
    return out[0] == "1 1"


def _snapshot() -> list:
    """A comparable structure of everything shown in the tree."""
    sessions = []
    for session in tmux.server.sessions:
        if session.session_name.startswith(VIEWER_PREFIX):
            continue
        windows = []
        for window in session.windows:
            panes = [(pane.pane_id, pane.pane_index, pane.pane_current_command)
                     for pane in window.panes]
            windows.append((window.window_index, window.window_name, panes))
        sessions.append((session.session_name, windows))
    return sessions


class TmuxComposeApp(App):
    TITLE = "tmux-compose"

    CSS = """
    #session-tree {
        width: 32;
        min-width: 24;
        border-right: solid $primary;
    }
    #viewer {
        width: 1fr;
        height: 1fr;
    }
    #placeholder {
        content-align: center middle;
        height: 1fr;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+t", "toggle_focus", "Tree/Terminal", priority=True),
    ]

    def __init__(self, **kwargs):
        # Pass ANSI colors through to the real terminal instead of
        # converting them to RGB via Textual's built-in ANSI theme, so pane
        # content is rendered with the user's own terminal palette.
        kwargs.setdefault("ansi_color", True)
        super().__init__(**kwargs)
        self._tree_state: list = []
        self._zoomed_pane: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Tree("sessions", id="session-tree")
            with Container(id="viewer"):
                yield Static("Select a pane on the left", id="placeholder")
        yield Footer()

    def on_mount(self) -> None:
        self._kill_viewer()
        tree = self.query_one(Tree)
        tree.show_root = False
        tree.auto_expand = False  # selecting a node must not toggle it
        tree.focus()
        self._refresh_tree()
        self.set_interval(2.0, self._refresh_tree)

    # --- session tree ------------------------------------------------------

    def _refresh_tree(self) -> None:
        state = _snapshot()
        if state == self._tree_state:
            return
        self._tree_state = state

        tree = self.query_one(Tree)
        tree.clear()
        for session_name, windows in state:
            session_node = tree.root.add(session_name, expand=True)
            # The tree is always fully expanded; nodes cannot be collapsed.
            session_node.allow_expand = False
            for window_index, window_name, panes in windows:
                window_node = session_node.add(
                    f"{window_index}: {window_name}", expand=True,
                    data={
                        "session": session_name,
                        "window_index": window_index,
                    },
                )
                window_node.allow_expand = False
                for pane_id, pane_index, pane_command in panes:
                    window_node.add_leaf(
                        f"{pane_index}: {pane_command}",
                        data={
                            "session": session_name,
                            "window_index": window_index,
                            "pane_id": pane_id,
                        },
                    )
        tree.root.expand()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self._view(event.node.data)

    # --- viewer ------------------------------------------------------------

    def _view(self, target: dict) -> None:
        """Show a window in the viewer; if ``target`` names a pane, select
        and zoom it so it fills the panel."""
        viewer = _viewer_name()
        self._kill_viewer()

        # Grouped session pointed at the chosen window/pane. Window indexes
        # are shared within a group; pane ids are globally unique.
        tmux.server.cmd("new-session", "-d", "-s", viewer,
                        "-t", target["session"])
        # Keys forwarded into the pane must never act as the tmux prefix.
        tmux.server.cmd("set-option", "-t", viewer, "prefix", "None")
        tmux.server.cmd("set-option", "-t", viewer, "prefix2", "None")
        tmux.server.cmd("set-option", "-t", viewer, "status", "off")
        tmux.server.cmd("select-window",
                        "-t", f"{viewer}:{target['window_index']}")
        if "pane_id" in target:
            tmux.server.cmd("select-pane", "-t", target["pane_id"])
            self._zoom(target["pane_id"])

        container = self.query_one("#viewer", Container)
        container.remove_children()
        terminal = TerminalEmulator(_attach_argv(viewer))
        container.mount(terminal)
        terminal.focus()

    def _zoom(self, pane_id: str) -> None:
        """Zoom ``pane_id`` so it fills its window. ``resize-pane -Z``
        toggles, so an existing zoom (possibly on another pane) is cleared
        first."""
        if _zoomed_on(pane_id) is not None:
            tmux.server.cmd("resize-pane", "-Z", "-t", pane_id)
        tmux.server.cmd("resize-pane", "-Z", "-t", pane_id)
        self._zoomed_pane = pane_id

    def _unzoom(self) -> None:
        """Undo our zoom, but only if it is still ours: the pane must exist
        and its window must still be zoomed on it."""
        pane_id, self._zoomed_pane = self._zoomed_pane, None
        if pane_id is not None and _zoomed_on(pane_id):
            tmux.server.cmd("resize-pane", "-Z", "-t", pane_id)

    def _kill_viewer(self) -> None:
        self._unzoom()
        tmux.kill_session(_viewer_name())

    def on_unmount(self) -> None:
        self._kill_viewer()

    # --- actions -----------------------------------------------------------

    def action_toggle_focus(self) -> None:
        tree = self.query_one(Tree)
        terminals = self.query(TerminalEmulator)
        if tree.has_focus and terminals:
            terminals.first().focus()
        else:
            tree.focus()

    async def action_quit(self) -> None:
        self.exit()


def run_tui() -> None:
    app = TmuxComposeApp()
    try:
        app.run()
    finally:
        tmux.kill_session(_viewer_name())
