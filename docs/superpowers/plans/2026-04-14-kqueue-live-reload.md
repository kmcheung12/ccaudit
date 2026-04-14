# kqueue Live Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Watch JSONL session files for changes using macOS kqueue and update the TUI live without restarting.

**Architecture:** A `FileWatcher` class runs on a background daemon thread, managing a kqueue instance via a command queue (so the main thread can add/remove watches safely). Directories are watched for new JSONL file creation (one fd per project dir); individual JSONL files are watched only for sessions currently visible in the tree, plus whichever session file has the most recent mtime. Callbacks use `app.call_from_thread()` to re-enter the Textual event loop safely.

**Tech Stack:** Python stdlib only — `select.kqueue`, `os`, `queue`, `threading`. No new dependencies.

---

## File Map

| File | Change |
|---|---|
| `parser/models.py` | Add `jsonl_path: str = ""` to `SessionStats` |
| `parser/loader.py` | Set `jsonl_path` in `SessionStats` constructor; add `apply_session_updates()` |
| `tui/watcher.py` | **Create** — `FileWatcher` class + `latest_jsonl_path()` + `find_session_by_path()` |
| `tui/tree.py` | Add `refresh_session_node()` and `add_session_node()` methods |
| `tui/app.py` | Create watcher on mount, handle expand/collapse events, implement reload callbacks |
| `tests/test_watcher.py` | **Create** — tests for pure helper functions |

---

## Task 1: Add `jsonl_path` to `SessionStats`

**Files:**
- Modify: `parser/models.py:71-76`
- Modify: `parser/loader.py:341-346`
- Test: `tests/test_watcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watcher.py
from pathlib import Path
from parser.loader import load_session
import tempfile, json, os

def _write_jsonl(path: Path, messages: list[dict]) -> None:
    with open(path, "w") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")

def _minimal_session_messages() -> list[dict]:
    return [
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "timestamp": "2026-04-14T10:00:00Z",
            "uuid": "aaa",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "hi",
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0, "output_tokens": 5},
            },
            "timestamp": "2026-04-14T10:00:01Z",
            "uuid": "bbb",
        },
    ]

def test_session_jsonl_path_is_set():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "abc123.jsonl"
        _write_jsonl(p, _minimal_session_messages())
        session = load_session(p)
        assert session.jsonl_path == str(p)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/alan/code/ccaudit && python3 -m pytest tests/test_watcher.py::test_session_jsonl_path_is_set -v
```

Expected: `FAILED` — `AttributeError: 'SessionStats' object has no attribute 'jsonl_path'`

- [ ] **Step 3: Add `jsonl_path` to `SessionStats`**

In `parser/models.py`, add the field after `first_timestamp`:

```python
@dataclass
class SessionStats:
    session_id: str
    display_name: str
    first_timestamp: Optional[str]
    exchanges: list[ExchangeStats] = field(default_factory=list)
    jsonl_path: str = ""
```

- [ ] **Step 4: Set `jsonl_path` in `load_session`**

In `parser/loader.py`, update the `SessionStats(...)` constructor call at the bottom of `load_session`:

```python
    return SessionStats(
        session_id=session_id,
        display_name=display_name,
        first_timestamp=first_timestamp,
        exchanges=exchanges,
        jsonl_path=str(jsonl_file),
    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python3 -m pytest tests/test_watcher.py::test_session_jsonl_path_is_set -v
```

Expected: `PASSED`

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add parser/models.py parser/loader.py tests/test_watcher.py
git commit -m "feat: add jsonl_path to SessionStats"
```

---

## Task 2: Add `apply_session_updates()` to loader

**Files:**
- Modify: `parser/loader.py` (add function after `load_session`)
- Test: `tests/test_watcher.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_watcher.py
from parser.loader import apply_session_updates

def test_apply_session_updates_appends_new_exchanges():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "abc123.jsonl"
        msgs = _minimal_session_messages()
        _write_jsonl(p, msgs)
        session = load_session(p)
        assert len(session.exchanges) == 1

        # Add a second exchange to the file
        second = [
            {
                "type": "user",
                "message": {"role": "user", "content": "again"},
                "timestamp": "2026-04-14T10:01:00Z",
                "uuid": "ccc",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "sure",
                    "usage": {"input_tokens": 8, "cache_read_input_tokens": 0,
                              "cache_creation_input_tokens": 0, "output_tokens": 3},
                },
                "timestamp": "2026-04-14T10:01:01Z",
                "uuid": "ddd",
            },
        ]
        _write_jsonl(p, msgs + second)
        updated = load_session(p)
        added = apply_session_updates(session, updated)
        assert added == 1
        assert len(session.exchanges) == 2

def test_apply_session_updates_no_change():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "abc123.jsonl"
        _write_jsonl(p, _minimal_session_messages())
        session = load_session(p)
        updated = load_session(p)
        added = apply_session_updates(session, updated)
        assert added == 0
        assert len(session.exchanges) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_watcher.py::test_apply_session_updates_appends_new_exchanges tests/test_watcher.py::test_apply_session_updates_no_change -v
```

Expected: `FAILED` — `ImportError: cannot import name 'apply_session_updates'`

- [ ] **Step 3: Implement `apply_session_updates` in `parser/loader.py`**

Add after the `load_session` function:

```python
def apply_session_updates(existing: SessionStats, updated: SessionStats) -> int:
    """Append new exchanges from `updated` to `existing` in-place.

    Returns the number of new exchanges added. Mutates `existing` directly
    so all existing references to the object remain valid.
    """
    old_count = len(existing.exchanges)
    new_count = len(updated.exchanges)
    if new_count <= old_count:
        return 0
    existing.exchanges.extend(updated.exchanges[old_count:])
    return new_count - old_count
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_watcher.py -v
```

Expected: all 3 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add parser/loader.py tests/test_watcher.py
git commit -m "feat: add apply_session_updates to loader"
```

---

## Task 3: Implement `FileWatcher` and helper functions

**Files:**
- Create: `tui/watcher.py`
- Test: `tests/test_watcher.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_watcher.py
from parser.models import GlobalStats, ProjectStats, SessionStats
from tui.watcher import latest_jsonl_path, find_session_by_path

def _make_loaded_project(sessions: list) -> ProjectStats:
    p = ProjectStats(project_slug="test", display_name="test")
    p.sessions = sessions
    p.loaded = True
    return p

def test_latest_jsonl_path_returns_most_recent(tmp_path):
    a = tmp_path / "aaa.jsonl"
    b = tmp_path / "bbb.jsonl"
    a.write_text("")
    b.write_text("")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))

    s_a = SessionStats(session_id="aaa", display_name="aaa", first_timestamp=None, jsonl_path=str(a))
    s_b = SessionStats(session_id="bbb", display_name="bbb", first_timestamp=None, jsonl_path=str(b))
    project = _make_loaded_project([s_a, s_b])
    global_stats = GlobalStats(projects=[project])

    result = latest_jsonl_path(global_stats.projects)
    assert result == str(b)

def test_latest_jsonl_path_skips_unloaded():
    project = ProjectStats(project_slug="x", display_name="x")
    project.loaded = False
    global_stats = GlobalStats(projects=[project])
    assert latest_jsonl_path(global_stats.projects) is None

def test_find_session_by_path():
    s = SessionStats(session_id="aaa", display_name="aaa", first_timestamp=None, jsonl_path="/tmp/aaa.jsonl")
    project = _make_loaded_project([s])
    global_stats = GlobalStats(projects=[project])

    found_project, found_session = find_session_by_path(global_stats.projects, "/tmp/aaa.jsonl")
    assert found_session is s
    assert found_project is project

def test_find_session_by_path_not_found():
    global_stats = GlobalStats(projects=[])
    p, s = find_session_by_path(global_stats.projects, "/tmp/nope.jsonl")
    assert p is None
    assert s is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_watcher.py::test_latest_jsonl_path_returns_most_recent tests/test_watcher.py::test_latest_jsonl_path_skips_unloaded tests/test_watcher.py::test_find_session_by_path tests/test_watcher.py::test_find_session_by_path_not_found -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'tui.watcher'`

- [ ] **Step 3: Create `tui/watcher.py`**

```python
# tui/watcher.py
from __future__ import annotations
import os
import queue
import select
import threading
from typing import Callable

from parser.models import ProjectStats, SessionStats

# kqueue vnode filter constants (macOS/BSD stdlib)
_KQ_FILTER_VNODE = -4
_KQ_EV_ADD    = 0x0001
_KQ_EV_DELETE = 0x0002
_KQ_EV_CLEAR  = 0x0020
_NOTE_WRITE   = 0x00000002
_NOTE_EXTEND  = 0x00000004


class FileWatcher:
    """
    macOS kqueue-based file system watcher.

    Watches project directories for new JSONL file creation (NOTE_WRITE on
    the directory fd) and individual JSONL files for appends (NOTE_WRITE |
    NOTE_EXTEND on the file fd).

    Runs on a background daemon thread. Commands (add/remove watches) are
    posted via a queue so the Textual main thread never blocks. The kqueue
    loop uses a 0.5 s timeout to drain the command queue between events.

    Callbacks are invoked from the background thread — callers must use
    app.call_from_thread() to re-enter the Textual event loop safely.
    """

    def __init__(
        self,
        on_file_changed: Callable[[str], None],
        on_dir_changed: Callable[[str], None],
    ) -> None:
        self._on_file_changed = on_file_changed
        self._on_dir_changed = on_dir_changed
        self._kq = select.kqueue()
        self._dir_fds: dict[str, int] = {}   # dir_path  → fd
        self._file_fds: dict[str, int] = {}  # jsonl_path → fd
        self._fd_to_path: dict[int, str] = {}
        self._cmd_q: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="kqueue-watcher"
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def watch_dir(self, path: str) -> None:
        """Queue a directory watch (fires on new file creation)."""
        self._cmd_q.put(("add_dir", path))

    def watch_file(self, path: str) -> None:
        """Queue a file watch (fires on append/write)."""
        self._cmd_q.put(("add_file", path))

    def unwatch_file(self, path: str) -> None:
        """Queue removal of a file watch and close its fd."""
        self._cmd_q.put(("rm_file", path))

    # ------------------------------------------------------------------ #
    # Background thread                                                    #
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._drain_commands()
            try:
                events = self._kq.control(None, 32, 0.5)
            except OSError:
                continue
            for ev in events:
                path = self._fd_to_path.get(ev.ident)
                if path is None:
                    continue
                if path in self._dir_fds:
                    self._on_dir_changed(path)
                else:
                    self._on_file_changed(path)
        self._close_all()

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd, path = self._cmd_q.get_nowait()
            except queue.Empty:
                break
            if cmd == "add_dir":
                self._add_dir(path)
            elif cmd == "add_file":
                self._add_file(path)
            elif cmd == "rm_file":
                self._rm_file(path)

    def _add_dir(self, path: str) -> None:
        if path in self._dir_fds or not os.path.isdir(path):
            return
        fd = os.open(path, os.O_RDONLY)
        self._dir_fds[path] = fd
        self._fd_to_path[fd] = path
        ev = select.kevent(
            fd,
            filter=_KQ_FILTER_VNODE,
            flags=_KQ_EV_ADD | _KQ_EV_CLEAR,
            fflags=_NOTE_WRITE,
        )
        self._kq.control([ev], 0)

    def _add_file(self, path: str) -> None:
        if path in self._file_fds or not os.path.isfile(path):
            return
        fd = os.open(path, os.O_RDONLY)
        self._file_fds[path] = fd
        self._fd_to_path[fd] = path
        ev = select.kevent(
            fd,
            filter=_KQ_FILTER_VNODE,
            flags=_KQ_EV_ADD | _KQ_EV_CLEAR,
            fflags=_NOTE_WRITE | _NOTE_EXTEND,
        )
        self._kq.control([ev], 0)

    def _rm_file(self, path: str) -> None:
        fd = self._file_fds.pop(path, None)
        if fd is None:
            return
        self._fd_to_path.pop(fd, None)
        ev = select.kevent(fd, filter=_KQ_FILTER_VNODE, flags=_KQ_EV_DELETE)
        try:
            self._kq.control([ev], 0)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def _close_all(self) -> None:
        for fd in list(self._file_fds.values()) + list(self._dir_fds.values()):
            try:
                os.close(fd)
            except OSError:
                pass
        self._file_fds.clear()
        self._dir_fds.clear()
        self._fd_to_path.clear()
        try:
            self._kq.close()
        except OSError:
            pass


# ------------------------------------------------------------------ #
# Pure helper functions (no kqueue dependency — easy to test)         #
# ------------------------------------------------------------------ #

def latest_jsonl_path(projects: list[ProjectStats]) -> str | None:
    """Return the jsonl_path of the session with the most recent mtime.

    Scans all loaded sessions. Returns None if no loaded sessions exist.
    Used to ensure the actively-written session is always watched even
    when it hasn't been expanded in the tree.
    """
    latest_path: str | None = None
    latest_mtime = 0.0
    for project in projects:
        if not project.loaded:
            continue
        for session in project.sessions:
            if not session.jsonl_path:
                continue
            try:
                mtime = os.path.getmtime(session.jsonl_path)
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_path = session.jsonl_path
            except OSError:
                pass
    return latest_path


def find_session_by_path(
    projects: list[ProjectStats], jsonl_path: str
) -> tuple[ProjectStats | None, SessionStats | None]:
    """Return (project, session) for the given jsonl_path, or (None, None)."""
    for project in projects:
        for session in project.sessions:
            if session.jsonl_path == jsonl_path:
                return project, session
    return None, None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_watcher.py -v
```

Expected: all 7 tests `PASSED`

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add tui/watcher.py tests/test_watcher.py
git commit -m "feat: add FileWatcher and helper functions"
```

---

## Task 4: Add `refresh_session_node` and `add_session_node` to `StatsTree`

**Files:**
- Modify: `tui/tree.py`

There are no meaningful unit tests for these (they require a running Textual app). Correctness is verified in Task 6 (manual integration test).

- [ ] **Step 1: Add `refresh_session_node` to `StatsTree`**

Add after the `filter` method in `tui/tree.py`:

```python
def refresh_session_node(self, session: SessionStats) -> None:
    """Update tree after new exchanges were appended to `session` in-place.

    Updates the session node label. If the session node is expanded,
    appends tree nodes for exchanges beyond the previously rendered count.
    """
    from parser.models import ExchangeStats
    tree = self.query_one("#stats-tree", _NavTree)
    session_node = self._find_node_by_data(tree.root, session)
    if session_node is None:
        return
    label = f"🗂 {session.display_name}"
    if not session.exchanges:
        label += " (empty)"
    session_node.label = label
    if not session_node.is_expanded:
        return
    existing_count = sum(
        1 for child in session_node.children
        if isinstance(child.data, ExchangeStats)
    )
    for exchange in session.exchanges[existing_count:]:
        prefix = "⚡" if exchange.after_compact else "↩"
        t_node = session_node.add(
            f"{prefix} exchange {exchange.exchange_number}",
            data=exchange,
            expand=False,
        )
        for cat_name, tokens in exchange.category_breakdown.category_totals().items():
            if tokens == 0:
                continue
            t_node.add_leaf(f"  {cat_name}: {tokens:,}", data=(exchange, cat_name))
```

- [ ] **Step 2: Add `add_session_node` to `StatsTree`**

Add after `refresh_session_node`:

```python
def add_session_node(self, project: ProjectStats, session: SessionStats) -> None:
    """Insert a new session node under an already-expanded project node.

    No-ops if the project node is not currently expanded (the node will be
    rendered correctly the next time the user expands the project).
    """
    tree = self.query_one("#stats-tree", _NavTree)
    project_node = self._find_node_by_data(tree.root, project)
    if project_node is None or not project_node.is_expanded:
        return
    label = f"🗂 {session.display_name}"
    if not session.exchanges:
        label += " (empty)"
    s_node = project_node.add(label, data=session, expand=False)
    if session.first_timestamp:
        s_node.tooltip = session.first_timestamp[:19].replace("T", " ")
    for exchange in session.exchanges:
        prefix = "⚡" if exchange.after_compact else "↩"
        t_node = s_node.add(
            f"{prefix} exchange {exchange.exchange_number}",
            data=exchange,
            expand=False,
        )
        for cat_name, tokens in exchange.category_breakdown.category_totals().items():
            if tokens == 0:
                continue
            t_node.add_leaf(f"  {cat_name}: {tokens:,}", data=(exchange, cat_name))
```

- [ ] **Step 3: Run full suite to confirm nothing broke**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tui/tree.py
git commit -m "feat: add refresh_session_node and add_session_node to StatsTree"
```

---

## Task 5: Integrate FileWatcher into `CCAuditApp`

**Files:**
- Modify: `tui/app.py`

- [ ] **Step 1: Add imports at the top of `tui/app.py`**

```python
from pathlib import Path
from parser.loader import PROJECTS_DIR, load_session, apply_session_updates
from tui.watcher import FileWatcher, latest_jsonl_path, find_session_by_path
```

The full import block at the top of `tui/app.py` should be:

```python
from __future__ import annotations
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual import events
from parser.models import GlobalStats, ProjectStats, SessionStats, ExchangeStats, TurnStats
from parser.loader import PROJECTS_DIR, load_session, apply_session_updates
from tui.tree import StatsTree, NodeSelected
from tui.detail import DetailPane
from tui.watcher import FileWatcher, latest_jsonl_path, find_session_by_path
```

Note: remove `TurnStats` from the import if it's not already there (it's `ExchangeStats` now).

- [ ] **Step 2: Add `_watcher` and `_latest_watched` attributes and start watcher in `on_mount`**

Replace the existing `on_mount` method:

```python
def on_mount(self) -> None:
    detail = self.query_one("#detail-pane", DetailPane)
    detail.update(self._global)

    self._latest_watched: str | None = None
    self._watcher = FileWatcher(
        on_file_changed=lambda p: self.call_from_thread(self._on_jsonl_changed, p),
        on_dir_changed=lambda p: self.call_from_thread(self._on_dir_changed, p),
    )
    for project in self._global.projects:
        base = Path(project.projects_dir) if project.projects_dir else PROJECTS_DIR
        project_dir = base / project.project_slug
        if project_dir.is_dir():
            self._watcher.watch_dir(str(project_dir))
    self._watch_latest()
    self._watcher.start()
```

- [ ] **Step 3: Add `_watch_latest` helper**

Add after `on_mount`:

```python
def _watch_latest(self) -> None:
    """Watch the JSONL file with the most recent mtime."""
    path = latest_jsonl_path(self._global.projects)
    if path and path != self._latest_watched:
        self._latest_watched = path
        self._watcher.watch_file(path)
```

- [ ] **Step 4: Handle tree expand/collapse to watch/unwatch session files**

Add after `_watch_latest`:

```python
def on_tree_node_expanded(self, event) -> None:
    node = event.node
    if isinstance(node.data, ProjectStats):
        for session in node.data.sessions:
            if session.jsonl_path:
                self._watcher.watch_file(session.jsonl_path)

def on_tree_node_collapsed(self, event) -> None:
    node = event.node
    if isinstance(node.data, ProjectStats):
        for session in node.data.sessions:
            if session.jsonl_path and session.jsonl_path != self._latest_watched:
                self._watcher.unwatch_file(session.jsonl_path)
```

- [ ] **Step 5: Add `_on_jsonl_changed` callback**

Add after `on_tree_node_collapsed`:

```python
def _on_jsonl_changed(self, jsonl_path: str) -> None:
    """Called on the Textual thread when a watched JSONL file is modified."""
    project, session = find_session_by_path(self._global.projects, jsonl_path)
    if session is None:
        return
    try:
        updated = load_session(Path(jsonl_path))
    except Exception:
        return
    added = apply_session_updates(session, updated)
    if added > 0:
        self.query_one("#tree-pane", StatsTree).refresh_session_node(session)
```

- [ ] **Step 6: Add `_on_dir_changed` callback**

Add after `_on_jsonl_changed`:

```python
def _on_dir_changed(self, dir_path: str) -> None:
    """Called on the Textual thread when a watched directory gets a new JSONL file."""
    project = None
    for p in self._global.projects:
        base = Path(p.projects_dir) if p.projects_dir else PROJECTS_DIR
        if str(base / p.project_slug) == dir_path:
            project = p
            break
    if project is None or not project.loaded:
        return
    existing_paths = {s.jsonl_path for s in project.sessions}
    for jsonl_file in sorted(Path(dir_path).glob("*.jsonl")):
        path_str = str(jsonl_file)
        if path_str in existing_paths:
            continue
        try:
            new_session = load_session(jsonl_file)
        except Exception:
            continue
        project.sessions.append(new_session)
        self._watcher.watch_file(path_str)
        self.query_one("#tree-pane", StatsTree).add_session_node(project, new_session)
    self._watch_latest()
```

- [ ] **Step 7: Run full test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add tui/app.py
git commit -m "feat: integrate kqueue FileWatcher into CCAuditApp for live reload"
```

---

## Task 6: Manual Integration Test

- [ ] **Step 1: Start the app pointed at the current project**

```bash
python3 main.py -d .
```

Expand the ccaudit project node and find the most recent session.

- [ ] **Step 2: In a second terminal, send a message to Claude Code**

While the ccaudit TUI is open, type any message in a Claude Code session for this project. Wait for the response.

- [ ] **Step 3: Verify live update**

Within ~1 second of Claude's response completing, the exchange count on the session node in the tree should increment without any user interaction or restart.

- [ ] **Step 4: Test new session detection**

Start a brand new Claude Code session (`claude`) in the same project directory. After Claude responds to the first message, verify a new session node appears in the ccaudit tree.

- [ ] **Step 5: Commit if manual tests pass**

```bash
git add -A
git commit -m "feat: kqueue live reload — watch JSONL for new exchanges and sessions"
```
