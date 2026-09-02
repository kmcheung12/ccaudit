# tui/app.py
from __future__ import annotations
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable, Tree
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual import events
from parser.models import GlobalStats, ProjectStats, SessionStats, ExchangeStats
from parser.loader import load_session, apply_session_updates
from tui.tree import StatsTree, NodeSelected
from tui.detail import DetailPane
from tui.watcher import FileWatcher, latest_jsonl_path, find_session_by_path


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
        self._watcher = FileWatcher(
            on_file_changed=lambda p: self.call_from_thread(self._on_jsonl_changed, p),
            on_dir_changed=lambda p: self.call_from_thread(self._on_dir_changed, p),
        )
        for project in self._global.projects:
            if not project.claude_dir:
                continue  # Codex-only project has no Claude directory to watch
            if Path(project.claude_dir).is_dir():
                self._watcher.watch_dir(project.claude_dir)
        self._watch_latest()
        self._watcher.start()

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
        project, session = find_session_by_path(self._global.projects, jsonl_path)
        if session is None:
            return
        try:
            updated = load_session(Path(jsonl_path))
        except Exception:
            return
        added = apply_session_updates(session, updated)
        if added > 0:
            tree_pane = self.query_one("#tree-pane", StatsTree)
            tree_pane.refresh_session_node(session)
            if tree_pane.current_node_data() is session:
                self.query_one("#detail-pane", DetailPane).update(session)

    def _on_dir_changed(self, dir_path: str) -> None:
        """Called on the Textual thread when a watched directory gets a new JSONL file."""
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
