# tui/tree.py
from __future__ import annotations
from textual.widgets import Tree
from textual.widgets.tree import TreeNode
from textual.widget import Widget
from textual.message import Message
from parser.models import GlobalStats, ProjectStats
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
        self._add_project_nodes(tree.root, self._global.projects)
        yield tree

    def _add_project_nodes(self, root: TreeNode, projects: list[ProjectStats]) -> None:
        """Add project nodes with placeholder children to root."""
        for project in projects:
            node = root.add(
                f"📁 {project.display_name}",
                data=project,
                expand=False,
            )
            node.add_leaf("Loading...", data=None)

    def _populate_project_node(self, node: TreeNode, project: ProjectStats) -> None:
        """Populate a project node with its sessions (called lazily on first expand)."""
        load_project(project)
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
                for cat_name, tokens in turn.category_breakdown.category_totals().items():
                    if tokens == 0:
                        continue
                    t_node.add_leaf(f"  {cat_name}: {tokens:,}", data=(turn, cat_name))

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        project = node.data
        if not isinstance(project, ProjectStats):
            return
        if project.loaded:
            return
        self._populate_project_node(node, project)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.post_message(NodeSelected(event.node.data))

    def filter(self, query: str) -> None:
        """Filter project nodes by case-insensitive substring match on display name.

        Removes non-matching project nodes from the tree and re-adds matching ones.
        Clears query (empty string) restores all projects.
        """
        tree = self.query_one("#stats-tree", Tree)
        query = query.lower().strip()

        # Remove all current project children from root
        tree.root.remove_children()

        # Re-add only matching projects (or all if no query)
        matching = [
            p for p in self._global.projects
            if not query or query in p.display_name.lower()
        ]
        self._add_project_nodes(tree.root, matching)
