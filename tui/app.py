# tui/app.py
from __future__ import annotations
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable, Tree
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual import events
from parser.models import GlobalStats, ProjectStats, SessionStats, ExchangeStats
from parser.loader import load_session, apply_session_updates, path_to_slug
from parser.codex_loader import load_codex_session, read_session_cwd
from tui.tree import StatsTree, NodeSelected
from tui.detail import DetailPane
from tui.watcher import FileWatcher, latest_jsonl_path, find_session_by_path

# Codex stores rollouts by date (<sessions>/YYYY/MM/DD/rollout-*.jsonl), so only
# the newest day directories can ever gain a file. Watching every day directory
# would burn a file descriptor per day of history for no benefit; two covers the
# midnight boundary and any clock skew.
_CODEX_WATCHED_DAYS = 2


def _sorted_subdirs(path: Path) -> list[Path]:
    """Immediate subdirectories of `path`, name-sorted. Empty if unreadable."""
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        return []


class CCAuditApp(App):
    """ccaudit — Claude Code Token Usage Explorer."""

    TITLE = "CC Audit"

    CSS = """
    #main {
        layout: horizontal;
        height: 1fr;
    }
    #filter-bar {
        height: 3;
        display: none;
    }
    #filter-bar.visible {
        display: block;
    }
    DetailPane {
        width: 1fr;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "open_filter", "Filter"),
        Binding("escape", "close_filter", "Close filter", show=False),
    ]

    def __init__(self, projects: list[ProjectStats], **kwargs):
        super().__init__(**kwargs)
        self._global = GlobalStats(projects=projects)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            with Horizontal(id="main"):
                yield StatsTree(self._global, id="tree-pane")
                yield DetailPane(id="detail-pane")
            yield Input(placeholder="Filter projects/sessions...", id="filter-bar")
        yield Footer()

    def on_mount(self) -> None:
        detail = self.query_one("#detail-pane", DetailPane)
        detail.update(self._global)

        self._latest_watched: str | None = None
        self._codex_root = self._codex_sessions_root()
        self._codex_dirs: set[str] = set()      # sessions root + year + month levels
        self._codex_day_dirs: set[str] = set()  # YYYY/MM/DD dirs that hold rollouts
        self._codex_pending: set[str] = set()   # rollouts seen before they were readable
        self._watcher = FileWatcher(
            on_file_changed=lambda p: self.call_from_thread(self._on_jsonl_changed, p),
            on_dir_changed=lambda p: self.call_from_thread(self._on_dir_changed, p),
        )
        for project in self._global.projects:
            if not project.claude_dir:
                continue  # Codex-only project has no Claude directory to watch
            if Path(project.claude_dir).is_dir():
                self._watcher.watch_dir(project.claude_dir)
        self._watch_codex_tree(scan_new=False)
        self._watch_latest()
        self._watcher.start()

    def _codex_sessions_root(self) -> Path | None:
        """The Codex sessions root, inferred from any known rollout path.

        Rollouts live at <root>/YYYY/MM/DD/rollout-*.jsonl, so the root is four
        levels up. Inferring beats importing CODEX_SESSIONS_DIR: it follows
        whatever directory discovery actually used, and is None (nothing to
        watch) when Codex logs were excluded or none exist.
        """
        for project in self._global.projects:
            for codex_file in project.codex_files:
                parents = Path(codex_file).parents
                if len(parents) > 3:
                    return parents[3]
        return None

    def _watch_codex_tree(self, scan_new: bool = True) -> None:
        """Watch the Codex rollout directories: root, year and month levels, newest days.

        Watching the ancestor levels is what makes a rollover visible — creating
        .../2026/09/03/ is a write to .../2026/09/, and a new month is a write to
        .../2026/. Re-running this after such an event picks up the new
        directory; the watcher de-dupes, so repeat calls are cheap.
        """
        if self._codex_root is None or not self._codex_root.is_dir():
            return
        ancestors = [self._codex_root]
        day_dirs: list[Path] = []
        for year in _sorted_subdirs(self._codex_root):
            ancestors.append(year)
            for month in _sorted_subdirs(year):
                ancestors.append(month)
                day_dirs.extend(_sorted_subdirs(month))
        for ancestor in ancestors:
            self._codex_dirs.add(str(ancestor))
            self._watcher.watch_dir(str(ancestor))
        for day_dir in sorted(day_dirs)[-_CODEX_WATCHED_DAYS:]:
            path_str = str(day_dir)
            if path_str in self._codex_day_dirs:
                continue
            self._codex_day_dirs.add(path_str)
            self._watcher.watch_dir(path_str)
            if scan_new:
                # A day directory is created together with its first rollout,
                # so the file is already there by the time we get here.
                self._scan_codex_dir(path_str)

    def _watch_latest(self) -> None:
        """Watch the JSONL file with the most recent mtime."""
        path = latest_jsonl_path(self._global.projects)
        if path and path != self._latest_watched:
            self._latest_watched = path
            self._watcher.watch_file(path)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        if isinstance(node.data, ProjectStats):
            for session in node.data.sessions:
                if session.jsonl_path:
                    self._watcher.watch_file(session.jsonl_path)

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        node = event.node
        if isinstance(node.data, ProjectStats):
            for session in node.data.sessions:
                if session.jsonl_path and session.jsonl_path != self._latest_watched:
                    self._watcher.unwatch_file(session.jsonl_path)

    def _on_jsonl_changed(self, jsonl_path: str) -> None:
        """Called on the Textual thread when a watched JSONL file is modified."""
        if jsonl_path in self._codex_pending:
            # A rollout that appeared before its session_meta line was flushed;
            # this write is our chance to route it.
            if not self._adopt_codex_file(jsonl_path):
                return
            self._codex_pending.discard(jsonl_path)
            self._watch_latest()
        project, session = find_session_by_path(self._global.projects, jsonl_path)
        if session is None:
            return
        parse = load_codex_session if jsonl_path in project.codex_files else load_session
        try:
            updated = parse(Path(jsonl_path))
        except Exception:
            return
        if apply_session_updates(session, updated) == 0:
            return
        self.query_one("#tree-pane", StatsTree).refresh_session_node(session)
        self._refresh_detail_for(session)

    def _refresh_detail_for(self, session: SessionStats) -> None:
        """Re-render the detail pane if it is currently showing part of `session`."""
        data = self.query_one("#tree-pane", StatsTree).current_node_data()
        detail = self.query_one("#detail-pane", DetailPane)
        if data is session:
            detail.update(session)
        elif isinstance(data, tuple) and len(data) == 2:
            exchange, cat_name = data
            if any(e is exchange for e in session.exchanges):
                detail.update_category(exchange, cat_name)
        elif isinstance(data, ExchangeStats) and any(e is data for e in session.exchanges):
            detail.update_exchange(data)

    def _on_dir_changed(self, dir_path: str) -> None:
        """Called on the Textual thread when a watched directory gets a new JSONL file."""
        if dir_path in self._codex_dirs:
            self._watch_codex_tree()  # a new year / month / day directory appeared
        elif dir_path in self._codex_day_dirs:
            self._scan_codex_dir(dir_path)
        else:
            self._scan_claude_dir(dir_path)

    def _scan_claude_dir(self, dir_path: str) -> None:
        """Adopt new JSONL files in a Claude project directory.

        A Claude project directory belongs to exactly one project, so every file
        in it is that project's.
        """
        project = None
        for p in self._global.projects:
            if p.claude_dir and p.claude_dir == dir_path:
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

    def _scan_codex_dir(self, dir_path: str) -> None:
        """Adopt rollouts in a Codex day directory that we haven't seen yet.

        Unlike a Claude project directory, one day directory holds the rollouts
        of *every* working directory used that day, so each file is routed
        individually by the cwd recorded in its own session_meta line.
        """
        known = set(self._codex_pending)
        for project in self._global.projects:
            known.update(project.codex_files)
        for jsonl_file in sorted(Path(dir_path).glob("rollout-*.jsonl")):
            path_str = str(jsonl_file)
            if path_str in known:
                continue
            if not self._adopt_codex_file(path_str):
                self._codex_pending.add(path_str)
                self._watcher.watch_file(path_str)  # retry on its first write
        self._watch_latest()

    def _adopt_codex_file(self, jsonl_path: str) -> bool:
        """Attach a rollout to the project whose working directory it ran in.

        Returns False when the file cannot be routed *yet* — a rollout exists
        before its session_meta line is flushed, and may be mid-write or corrupt
        — so the caller can retry it later. A rollout from a directory with no
        project node counts as resolved: there is nothing to attach it to.
        """
        cwd = read_session_cwd(Path(jsonl_path))
        if not cwd:
            return False
        slug = path_to_slug(Path(cwd))
        project = next(
            (p for p in self._global.projects if p.project_slug == slug), None
        )
        if project is None:
            return True
        if not project.loaded:
            # load_project() will read it when the user expands the project node.
            project.codex_files.append(jsonl_path)
            return True
        try:
            new_session = load_codex_session(Path(jsonl_path))
        except Exception:
            return False
        project.codex_files.append(jsonl_path)
        project.sessions.append(new_session)
        self._watcher.watch_file(jsonl_path)
        self.query_one("#tree-pane", StatsTree).add_session_node(project, new_session)
        return True

    def on_node_selected(self, event: NodeSelected) -> None:
        detail = self.query_one("#detail-pane", DetailPane)
        data = event.data
        # Category-level node: (exchange, cat_name) tuple
        if isinstance(data, tuple) and len(data) == 2:
            exchange, cat_name = data
            detail.update_category(exchange, cat_name)
        elif isinstance(data, ExchangeStats):
            detail.update_exchange(data)
            if event.sync_tree:
                self.query_one("#tree-pane", StatsTree).select_node(data)
        else:
            detail.update(data)
            if event.sync_tree:
                self.query_one("#tree-pane", StatsTree).select_node(data)

    def action_open_filter(self) -> None:
        bar = self.query_one("#filter-bar", Input)
        bar.add_class("visible")
        bar.focus()

    def action_close_filter(self) -> None:
        bar = self.query_one("#filter-bar", Input)
        bar.remove_class("visible")
        bar.value = ""
        tree = self.query_one("#tree-pane", StatsTree)
        tree.filter("")
        self.query_one("#tree-pane").focus()

    def on_key(self, event: events.Key) -> None:
        tree_pane = self.query_one("#tree-pane", StatsTree)
        detail_pane = self.query_one("#detail-pane", DetailPane)
        if event.key == "right" and tree_pane.has_focus_within:
            detail_pane.query_one("#category-table", DataTable).focus()
            event.stop()
        elif event.key == "left" and detail_pane.has_focus_within:
            tree_pane.query_one("#stats-tree").focus()
            event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-bar":
            tree = self.query_one("#tree-pane", StatsTree)
            tree.filter(event.value)
