# tests/test_tree.py
import asyncio

import pytest
from textual.app import App, ComposeResult

from parser.models import (
    CategoryBreakdown, ExchangeStats, SessionStats, ProjectStats, GlobalStats,
)
from tui.tree import StatsTree, _NavTree


def make_exchange(timestamp="2026-09-02T21:05:07Z"):
    return ExchangeStats(
        exchange_number=1, timestamp=timestamp,
        input_tokens=100, cache_read_tokens=0, cache_create_tokens=0,
        output_tokens=10, category_breakdown=CategoryBreakdown(),
    )


def make_global(display_name="01a0614d ⤷guardian"):
    session = SessionStats(
        session_id="01a0614d-1111-2222-3333-444455556666",
        display_name=display_name,
        exchanges=[make_exchange()],
    )
    project = ProjectStats(
        project_slug="-Users-alan-job-hunt",
        display_name="hunt",
        sessions=[session],
        loaded=True,
    )
    return GlobalStats(projects=[project]), project, session


class TreeOnlyApp(App):
    """Minimal host so StatsTree renders with its own DEFAULT_CSS width."""

    def __init__(self, global_stats):
        super().__init__()
        self._global = global_stats

    def compose(self) -> ComposeResult:
        yield StatsTree(self._global)


def rendered_lines(app) -> list[str]:
    return [
        "".join(segment.text for segment in strip)
        for strip in app.screen._compositor.render_strips()
    ]


async def render_expanded(global_stats, project, size):
    """Render the tree with `project` expanded and return the screen lines."""
    app = TreeOnlyApp(global_stats)
    async with app.run_test(size=size) as pilot:
        tree = app.query_one("#stats-tree", _NavTree)
        tree.root.expand()
        await pilot.pause()
        node = next(c for c in tree.root.children if c.data is project)
        node.expand()
        await pilot.pause()
        return rendered_lines(app)


@pytest.mark.parametrize("size", [(80, 24), (100, 24), (200, 40)])
def test_subagent_session_label_is_not_truncated(size):
    """A session label carrying a subagent marker must fit the tree pane.

    The expected text is derived from _session_label rather than hardcoded, so
    this stays a truncation test; the label format is pinned separately below.
    """
    global_stats, project, session = make_global()
    label = StatsTree(global_stats)._session_label(session)
    lines = asyncio.run(render_expanded(global_stats, project, size))
    assert any(label in line for line in lines), (
        f"label truncated at size {size}; tree pane rendered:\n"
        + "\n".join(lines[: size[1]])
    )


def test_plain_session_label_is_not_truncated():
    global_stats, project, session = make_global(display_name="01a0614d")
    label = StatsTree(global_stats)._session_label(session)
    lines = asyncio.run(render_expanded(global_stats, project, (80, 24)))
    assert any(label in line for line in lines)


def test_session_label_uses_month_day_and_minute_precision():
    """Year and seconds are dropped to keep the pane narrow; both live in the tooltip."""
    global_stats, _, session = make_global()
    tree = StatsTree(global_stats)
    assert tree._session_label(session) == "🗂 09-02 21:05 01a0614d ⤷guardian"


def test_session_tooltip_keeps_seconds():
    """Sessions within the same minute stay distinguishable via the tooltip."""
    global_stats, _, session = make_global()
    tooltip = StatsTree(global_stats)._session_tooltip(session)
    assert "21:05:07" in tooltip
    assert "first:" in tooltip and "last:" in tooltip


# refresh_session_node — trailing exchange keeps accumulating while it runs

def _leaf_labels(exchange_node):
    return [str(c.label).strip() for c in exchange_node.children]


def test_refresh_session_node_rerenders_trailing_exchange_leaves():
    """A running exchange's category totals grow in place; its leaves must follow."""
    global_stats, project, session = make_global()
    exchange = session.exchanges[0]
    exchange.category_breakdown.messages_tokens = 100

    async def run():
        app = TreeOnlyApp(global_stats)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(StatsTree)
            tree = pane.query_one("#stats-tree", _NavTree)
            project_node = tree.root.children[0]
            project_node.expand()
            await pilot.pause()
            session_node = project_node.children[0]
            session_node.expand()
            await pilot.pause()
            before = _leaf_labels(session_node.children[0])

            # Simulate the exchange accumulating more tokens in place.
            exchange.category_breakdown.messages_tokens = 250
            pane.refresh_session_node(session)
            await pilot.pause()
            after = _leaf_labels(session_node.children[0])
            return before, after

    before, after = asyncio.run(run())
    assert "Messages: 100" in before
    assert "Messages: 250" in after
    assert "Messages: 100" not in after


def test_refresh_session_node_appends_new_exchanges_without_duplicating():
    global_stats, project, session = make_global()

    async def run():
        app = TreeOnlyApp(global_stats)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(StatsTree)
            tree = pane.query_one("#stats-tree", _NavTree)
            project_node = tree.root.children[0]
            project_node.expand()
            await pilot.pause()
            session_node = project_node.children[0]
            session_node.expand()
            await pilot.pause()

            second = make_exchange(timestamp="2026-09-02T21:09:00Z")
            second.exchange_number = 2
            session.exchanges.append(second)
            pane.refresh_session_node(session)
            await pilot.pause()
            return [str(c.label) for c in session_node.children]

    labels = asyncio.run(run())
    assert len(labels) == 2
    assert "exchange 1" in labels[0]
    assert "exchange 2" in labels[1]
