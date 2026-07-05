# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Poetry manages the project; `tmux` must be on `PATH` (tests spawn real tmux servers).

```bash
poetry install                                # deps incl. dev group
poetry run pytest                             # full suite
poetry run pytest tests/test_tui.py           # one file
poetry run pytest tests/test_up.py -k focus   # one test by keyword
poetry run tmux-compose -f examples/simple.yml up    # run the CLI
poetry run tmux-compose tui                   # session-browser TUI
poetry build                                  # what CI builds
```

## What this is

A Python port of the Go [tmux-compose](https://github.com/kevinms/tmux-compose): a docker-compose-style `up`/`down`/`restart` for tmux sessions driven by a YAML file. The YAML schema and behavior are a 1:1 match of the original Go tool — don't change config semantics (including Go-style durations like `"1m30s"`, parsed in `model.py`) without keeping that compatibility.

## Architecture

Two halves: the orchestrator (`cli.py` → `model.py` → `runner.py` → `tmux.py`) and the TUI (`tui.py` → `terminal.py`).

### Orchestrator

- `tmux.py` holds the two module-level globals everything shares: `tmux.server` (a `libtmux.Server`; **all** tmux interaction goes through libtmux) and `tmux.shell_args` (set by the CLI, used for readychecks and pre/post commands, which run as local subprocesses rather than inside tmux).
- `model.py` defines `Project`/`Session`/`Window`/`Pane` with strict YAML loading (unknown keys raise). `Project.up()` first spawns every session/window/pane, then runs all commands concurrently; `restart` reruns only panes that have a `kill_cmd`.
- `runner.py` implements the dependency engine: every `Session`/`Window`/`Pane` is a `Runnable` with a `threading.Event`; `run_all` starts one thread per runnable, each waiting on its `depends_on` names. Readiness cascades: a pane is ready when its readycheck passes (or immediately, if none), a window when all its panes are, a session when all its windows are. Failed runnables still mark ready so dependents never hang; the first error is re-raised after the join.

### TUI

- `tui.py` is a Textual app: session/window/pane tree on the left, live viewer on the right. The viewer works by creating a hidden **grouped** tmux session (`new-session -t <target>`, named `_tui-<pid>`) so browsing never changes what other clients see; the selected pane is zoomed (`resize-pane -Z`) to fill the panel. Zoom and active-pane are window-level state shared with other clients — the un-zoom logic in `_unzoom` guards against clobbering someone else's zoom. The module docstring in `tui.py` explains the invariants.
- `terminal.py` is a from-scratch terminal widget: runs a command (the hidden tmux client) in a pty, parses output with pyte, renders via `render_line`, and translates Textual key **and mouse** events into escape sequences written back to the pty (mouse uses SGR encoding; the viewer session has tmux `mouse on`, so clicking a pane selects it).
- `textual` import is deferred in `cli.py` because it is slow; keep it out of the `up`/`down` path.

## Tests

Tests run against real, isolated tmux servers via libtmux's bundled pytest plugin: the `server` fixture, plus an autouse fixture in each test module that swaps `tmux.server`. TUI tests drive the app headless with Textual's pilot (`asyncio_mode = "auto"`). Expect timing-sensitive assertions to use the local `wait_for` helpers, not sleeps.
