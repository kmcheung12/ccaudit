from __future__ import annotations
from dataclasses import dataclass
from parser.models import (
    CategoryBreakdown, CategoryItem, TurnStats, SessionStats, ProjectStats, GlobalStats, CATEGORIES,
)
from textual.widgets import DataTable, Static
from textual.widget import Widget
from textual.containers import Vertical, VerticalScroll
from rich.text import Text


@dataclass
class TokenTotals:
    input_tokens: int
    cache_read: int
    cache_create: int
    output: int


# Category display colours (Rich style strings)
_CAT_STYLE = {
    "Messages":      "bright_magenta",
    "Skills":        "bright_yellow",
    "Memory":        "bright_green",
    "System Prompt": "white",
    "Tools":         "bright_blue",
    "Agents":        "bright_cyan",
}


def _get_totals(node) -> TokenTotals:
    """Extract raw token totals from any stats node."""
    if isinstance(node, TurnStats):
        return TokenTotals(
            input_tokens=node.input_tokens,
            cache_read=node.cache_read_tokens,
            cache_create=node.cache_create_tokens,
            output=node.output_tokens,
        )
    return TokenTotals(
        input_tokens=node.total_input_tokens,
        cache_read=node.total_cache_read_tokens,
        cache_create=node.total_cache_create_tokens,
        output=node.total_output_tokens,
    )


def build_rows(node) -> tuple[list[tuple[str, int, float]], TokenTotals]:
    """
    Build category rows and token totals for any stats node.

    Returns:
        rows: list of (category_name, tokens, percentage) sorted by tokens descending
        totals: TokenTotals with raw cache/output breakdown
    """
    if isinstance(node, TurnStats):
        cat_totals = node.category_breakdown.category_totals()
    else:
        cat_totals = node.category_totals()

    total = sum(cat_totals.values())
    rows = []
    for cat in CATEGORIES:
        tokens = cat_totals.get(cat, 0)
        if tokens == 0:
            continue
        pct = (tokens / total * 100) if total > 0 else 0.0
        rows.append((cat, tokens, pct))

    rows.sort(key=lambda r: r[1], reverse=True)
    return rows, _get_totals(node)


def build_category_rows(turn: TurnStats, cat_name: str) -> list[tuple[str, int]]:
    """
    Return individual items within a category for a turn.
    Returns list of (item_name, tokens) sorted by tokens descending.
    """
    bd = turn.category_breakdown
    mapping = {
        "Skills": bd.skills,
        "Memory": bd.memory,
        "Tools": bd.tools,
        "Agents": bd.agents,
    }
    items = mapping.get(cat_name, [])
    rows = [(item.name, item.tokens) for item in items if item.tokens > 0]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def build_turn_chart_legend() -> Text:
    """Build the colour legend for the turn chart."""
    result = Text()
    for cat, style in _CAT_STYLE.items():
        result.append("█", style=style)
        result.append(f" {cat}  ", style="dim")
    result.append("░", style="dim")
    result.append(" Cache read", style="dim")
    return result


def build_turn_chart_bars(session: SessionStats, bar_width: int = 28) -> Text:
    """
    Build per-turn stacked bar rows for a session (no legend).

    Each row = one turn. Fresh input is shown as coloured category segments.
    Cache-read tokens follow as dim '░' characters. ⚡ marks post-compact turns.
    """
    if not session.turns:
        return Text("(no turns)", style="dim")

    max_total = max(
        t.input_tokens + t.cache_create_tokens + t.cache_read_tokens
        for t in session.turns
    ) or 1

    result = Text()
    for i, turn in enumerate(session.turns):
        if i > 0:
            result.append("\n")

        fresh = turn.input_tokens + turn.cache_create_tokens
        total = fresh + turn.cache_read_tokens
        fresh_chars = max(1, round((fresh / max_total) * bar_width))
        cache_chars = round((turn.cache_read_tokens / max_total) * bar_width)

        # Category-coloured segments for fresh input
        cat_totals = turn.category_breakdown.category_totals()
        total_cat = sum(cat_totals.values()) or 1
        bar = Text()
        used = 0
        for cat in CATEGORIES:
            tokens = cat_totals.get(cat, 0)
            if tokens == 0:
                continue
            chars = round((tokens / total_cat) * fresh_chars)
            chars = min(chars, fresh_chars - used)
            if chars > 0:
                bar.append("█" * chars, style=_CAT_STYLE.get(cat, "white"))
                used += chars
        if used < fresh_chars:
            bar.append("█" * (fresh_chars - used), style="dim")

        # Cache-read shown as dim░
        if cache_chars > 0:
            bar.append("░" * cache_chars, style="dim")

        prefix = "⚡" if turn.after_compact else " "
        result.append(f"{prefix}T{turn.turn_number:2d} {total:6,}  ")
        result.append_text(bar)
        result.append(f"  →{turn.output_tokens:,}")

    return result


class DetailPane(Widget):
    """Right-pane widget: shows category breakdown + token totals for selected node."""

    DEFAULT_CSS = """
    DetailPane {
        layout: vertical;
    }
    #chart-section {
        display: none;
        border-top: solid $panel;
        height: 1fr;
    }
    #chart-legend {
        padding: 1 1 0 1;
        height: auto;
    }
    #chart-scroll {
        height: 1fr;
        padding: 0 1 1 1;
    }
    #chart-bars {
        height: auto;
    }
    #message-section {
        display: none;
        border-top: solid $panel;
        padding: 1 1;
        height: auto;
    }
    """

    def compose(self):
        yield DataTable(id="category-table")
        yield DataTable(id="totals-table")
        with Vertical(id="chart-section"):
            yield Static("", id="chart-legend")
            with VerticalScroll(id="chart-scroll"):
                yield Static("", id="chart-bars")
        yield Static("", id="message-section")

    def on_mount(self) -> None:
        cat_table = self.query_one("#category-table", DataTable)
        cat_table.add_columns("Category", "Tokens", "%")

        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.add_columns("", "Tokens")

    def _hide_extras(self) -> None:
        self.query_one("#chart-section", Vertical).display = False
        self.query_one("#message-section").display = False

    def _refresh_totals(self, totals: TokenTotals) -> None:
        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.clear()
        total_in = totals.input_tokens + totals.cache_read + totals.cache_create
        cache_pct = (totals.cache_read / total_in * 100) if total_in > 0 else 0.0
        totals_table.add_row("Input (fresh)", f"{totals.input_tokens:,}")
        totals_table.add_row("Cache read",    f"{totals.cache_read:,}  ({cache_pct:.0f}% hit)")
        totals_table.add_row("Cache write",   f"{totals.cache_create:,}")
        totals_table.add_row("Output",        f"{totals.output:,}")

    def update(self, node) -> None:
        """Refresh for GlobalStats, ProjectStats, or SessionStats."""
        self._hide_extras()
        rows, totals = build_rows(node)

        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear()
        for name, tokens, pct in rows:
            cat_table.add_row(name, f"{tokens:,}", f"{pct:.1f}%")

        self._refresh_totals(totals)

        # Session: show per-turn activity chart below the tables
        if isinstance(node, SessionStats):
            chart_section = self.query_one("#chart-section", Vertical)
            chart_section.display = True
            self.query_one("#chart-legend", Static).update(build_turn_chart_legend())
            self.query_one("#chart-bars", Static).update(build_turn_chart_bars(node))

    def update_turn(self, turn: TurnStats) -> None:
        """Refresh for a TurnStats node — shows category breakdown and message preview."""
        self._hide_extras()
        rows, totals = build_rows(turn)

        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear()
        for name, tokens, pct in rows:
            cat_table.add_row(name, f"{tokens:,}", f"{pct:.1f}%")

        self._refresh_totals(totals)

        # Show the human message and assistant response
        msg_widget = self.query_one("#message-section", Static)
        msg_widget.display = True

        user_preview = turn.user_text or "(no message text)"
        asst_preview = turn.assistant_text or "(no response text)"
        content = Text()
        content.append("User\n", style="bold bright_white")
        content.append(user_preview, style="dim")
        content.append("\n\nAssistant\n", style="bold bright_white")
        content.append(asst_preview, style="dim")
        if turn.files_read:
            content.append("\n\nFiles accessed\n", style="bold bright_white")
            for path in turn.files_read:
                content.append(f"  {path}\n", style="bright_blue")
        msg_widget.update(content)

    def update_category(self, turn: TurnStats, cat_name: str) -> None:
        """Show individual items within a category for a turn."""
        self._hide_extras()
        rows = build_category_rows(turn, cat_name)

        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear()
        if not rows:
            cat_table.add_row(f"(no items in {cat_name})", "", "")
        else:
            total = sum(t for _, t in rows)
            for name, tokens in rows:
                pct = (tokens / total * 100) if total > 0 else 0.0
                cat_table.add_row(name, f"{tokens:,}", f"{pct:.1f}%")

        _, totals = build_rows(turn)
        self._refresh_totals(totals)
