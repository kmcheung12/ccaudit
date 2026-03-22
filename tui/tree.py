# tui/tree.py
from __future__ import annotations
from textual.widgets import Tree
from textual.widgets.tree import TreeNode
from textual.widget import Widget
from textual.message import Message
from parser.models import GlobalStats, ProjectStats, SessionStats, TurnStats, CategoryBreakdown, CategoryItem
from parser.loader import load_project


class NodeSelected(Message):
    """Posted when the user selects a tree node."""
    def __init__(self, data) -> None:
        super().__init__()
        self.data = data


class StatsTree(Widget):
    """Left-pane tree widget."""

    DEFAULT_CSS = """
    StatsTree {
        width: 35;
        border-right: solid $panel;
    }
    """

    def __init__(self, global_stats: GlobalStats, **kwargs):
        super().__init__(**kwargs)
        self._global = global_stats

    def compose(self):
        tree: Tree = Tree("[ALL PROJECTS]", id="stats-tree")
        tree.root.data = self._global
        # Add project nodes (unloaded)
        for project in self._global.projects:
            node = tree.root.add(
                f"📁 {project.display_name}",
                data=project,
                expand=False,
            )
            # Add a placeholder child so the expand arrow appears
            node.add_leaf("Loading...", data=None)
        yield tree

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        project = node.data
        if not isinstance(project, ProjectStats):
            return
        if project.loaded:
            return
        # Lazy load
        load_project(project)
        # Remove placeholder
        node.remove_children()
        if project.load_error:
            node.add_leaf(f"⚠ {project.load_error}", data=None)
            return
        for session in project.sessions:
            label = f"🗂 {session.display_name}"
            if not session.turns:
                label += " (empty)"
            s_node = node.add(label, data=session, expand=False)
            if session.first_timestamp:
                s_node.tooltip = session.first_timestamp[:19].replace("T", " ")
            for turn in session.turns:
                prefix = "⚡" if turn.after_compact else "↩"
                t_node = s_node.add(
                    f"{prefix} turn {turn.turn_number}", data=turn, expand=False
                )
                # Category children
                for cat_name, tokens in turn.category_breakdown.category_totals().items():
                    if tokens == 0:
                        continue
                    t_node.add_leaf(f"  {cat_name}: {tokens:,}", data=(turn, cat_name))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.post_message(NodeSelected(event.node.data))

    def filter(self, query: str) -> None:
        """Show/hide nodes by case-insensitive substring match on label."""
        tree = self.query_one("#stats-tree", Tree)
        query = query.lower().strip()
        self._apply_filter(tree.root, query)

    def _apply_filter(self, node: TreeNode, query: str) -> bool:
        """Recursively show/hide. Returns True if node or any descendant matches."""
        label = str(node.label).lower()
        matches = not query or query in label
        child_matches = False
        for child in node.children:
            if self._apply_filter(child, query):
                child_matches = True
        visible = matches or child_matches
        node.allow_expand = visible
        return visible
