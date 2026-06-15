# tui/tree.py
from __future__ import annotations
from textual.widgets import Tree
from textual.widgets.tree import TreeNode
from textual.widget import Widget
from textual.message import Message
from textual.binding import Binding
from parser.models import GlobalStats, ProjectStats, ExchangeStats
from parser.loader import load_project


class NodeSelected(Message):
    """Posted when the user selects a tree node."""
    def __init__(self, data, sync_tree: bool = False) -> None:
        super().__init__()
        self.data = data
        self.sync_tree = sync_tree  # True only when source is bar chart (not the tree itself)


class _NavTree(Tree):
    """Tree subclass that maps left arrow to parent navigation."""

    BINDINGS = [
        *Tree.BINDINGS,
        Binding("left", "go_to_parent", "Go to parent", show=False),
    ]

    def action_go_to_parent(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded:
            node.collapse()
            return
        parent = node.parent
        if parent is not None and parent is not self.root:
            self.move_cursor(parent)


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
        tree = _NavTree("[ALL PROJECTS]", id="stats-tree")
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

    def _session_label(self, session) -> str:
        ts = session.last_timestamp
        ts_str = ts[:19].replace("T", " ") if ts else "no date"
        label = f"🗂 {ts_str} {session.display_name}"
        if not session.exchanges:
            label += " (empty)"
        return label

    def _populate_project_node_from_data(self, node: TreeNode, project: ProjectStats) -> None:
        """Render sessions for an already-loaded project (no disk I/O)."""
        if project.load_error:
            node.add_leaf(f"⚠ {project.load_error}", data=None)
            return
        sorted_sessions = sorted(
            project.sessions,
            key=lambda s: s.last_timestamp or "",
            reverse=True,
        )
        for session in sorted_sessions:
            s_node = node.add(self._session_label(session), data=session, expand=False)
            if session.first_timestamp:
                s_node.tooltip = session.first_timestamp[:19].replace("T", " ")
            for exchange in session.exchanges:
                prefix = "⚡" if exchange.after_compact else "↩"
                t_node = s_node.add(
                    f"{prefix} exchange {exchange.exchange_number}", data=exchange, expand=False
                )
                for cat_name, tokens in exchange.category_breakdown.category_totals().items():
                    if tokens == 0:
                        continue
                    t_node.add_leaf(f"  {cat_name}: {tokens:,}", data=(exchange, cat_name))

    def _populate_project_node(self, node: TreeNode, project: ProjectStats) -> None:
        """Load project from disk and populate node."""
        load_project(project)
        node.remove_children()
        self._populate_project_node_from_data(node, project)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        project = node.data
        if not isinstance(project, ProjectStats):
            return
        if project.loaded:
            # Already loaded — just re-populate from existing data
            node.remove_children()
            self._populate_project_node_from_data(node, project)
            return
        # First time — load from disk
        self._populate_project_node(node, project)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.post_message(NodeSelected(event.node.data))

    def _find_node_by_data(self, root: TreeNode, target) -> TreeNode | None:
        for child in root.children:
            if child.data is target:
                return child
            found = self._find_node_by_data(child, target)
            if found:
                return found
        return None

    def current_node_data(self):
        """Return the data of the currently focused tree node, or None."""
        cursor = self.query_one("#stats-tree", _NavTree).cursor_node
        return cursor.data if cursor else None

    def select_node(self, target_data) -> None:
        """Move the tree cursor to the node whose data is `target_data`, expanding ancestors."""
        tree = self.query_one("#stats-tree", _NavTree)
        node = self._find_node_by_data(tree.root, target_data)
        if node is None:
            return
        ancestor = node.parent
        while ancestor and ancestor is not tree.root:
            if not ancestor.is_expanded:
                ancestor.expand()
            ancestor = ancestor.parent
        tree.move_cursor(node)

    def filter(self, query: str) -> None:
        """Filter project nodes by case-insensitive substring match on display name.

        Removes non-matching project nodes from the tree and re-adds matching ones.
        Clears query (empty string) restores all projects.
        """
        tree = self.query_one("#stats-tree", _NavTree)
        query = query.lower().strip()

        # Remove all current project children from root
        tree.root.remove_children()

        # Re-add only matching projects (or all if no query)
        matching = [
            p for p in self._global.projects
            if not query or query in p.display_name.lower()
        ]
        self._add_project_nodes(tree.root, matching)

    def refresh_session_node(self, session) -> None:
        """Update tree after new exchanges were appended to `session` in-place.

        Updates the session node label. If the session node is expanded,
        appends tree nodes for exchanges beyond the previously rendered count.
        """
        tree = self.query_one("#stats-tree", _NavTree)
        session_node = self._find_node_by_data(tree.root, session)
        if session_node is None:
            return
        session_node.label = self._session_label(session)
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

    def add_session_node(self, project, session) -> None:
        """Insert a new session node under an already-expanded project node.

        No-ops if the project node is not currently expanded (the node will be
        rendered correctly the next time the user expands the project).
        Rebuilds children to maintain anti-chronological order.
        """
        tree = self.query_one("#stats-tree", _NavTree)
        project_node = self._find_node_by_data(tree.root, project)
        if project_node is None or not project_node.is_expanded:
            return
        project_node.remove_children()
        self._populate_project_node_from_data(project_node, project)
