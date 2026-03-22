# tui/app.py
from __future__ import annotations
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from parser.loader import list_projects
from parser.models import GlobalStats, TurnStats
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        projects = list_projects()
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
        # Category-level node: (turn, cat_name) tuple
        if isinstance(data, tuple) and len(data) == 2:
            turn, cat_name = data
            detail.update_category(turn, cat_name)
        elif isinstance(data, TurnStats):
            detail.update_turn(data)
        else:
            detail.update(data)

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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-bar":
            tree = self.query_one("#tree-pane", StatsTree)
            tree.filter(event.value)
