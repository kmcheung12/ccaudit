# tui/app.py
from __future__ import annotations
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual import events
from parser.models import GlobalStats, ProjectStats, ExchangeStats
from tui.tree import StatsTree, NodeSelected
from tui.detail import DetailPane


class CCAuditApp(App):
    """ccaudit — Claude Code Token Usage Explorer."""

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
