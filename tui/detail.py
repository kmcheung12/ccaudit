from __future__ import annotations
import json
from dataclasses import dataclass, field
from parser.models import (
    CategoryBreakdown, CategoryItem, TurnStats, SessionStats, ProjectStats, GlobalStats, CATEGORIES,
)
from parser.loader import load_project
from textual.widgets import DataTable, Static
from textual.widget import Widget
from textual.containers import Vertical, VerticalScroll
from textual.binding import Binding
from rich.text import Text


@dataclass
class TokenTotals:
    input_tokens: int
    cache_read: int
    cache_create: int
    output: int


@dataclass
class BarRow:
    label: str
    data: object          # node data posted as NodeSelected on Enter
    category_totals: dict[str, int]
    fresh_tokens: int     # input + cache_create
    cache_tokens: int     # cache_read
    output_tokens: int = 0


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


def build_chart_legend() -> Text:
    """Build the colour legend shared by all bar charts (same order as bar segments)."""
    result = Text()
    for cat in CATEGORIES:
        style = _CAT_STYLE.get(cat, "white")
        result.append("█", style=style)
        result.append(f" {cat}  ", style="dim")
    result.append("░", style="dim")
    result.append(" Cache read", style="dim")
    return result


# --- Bar row builders ---

def _session_bar_rows(node: GlobalStats) -> list[BarRow]:
    """One bar per loaded project."""
    rows = []
    for project in node.projects:
        if not project.loaded or not project.sessions:
            continue
        rows.append(BarRow(
            label=project.display_name[:14],
            data=project,
            category_totals=project.category_totals(),
            fresh_tokens=project.total_input_tokens + project.total_cache_create_tokens,
            cache_tokens=project.total_cache_read_tokens,
            output_tokens=project.total_output_tokens,
        ))
    return rows


def _session_bar_rows_for_project(node: ProjectStats) -> list[BarRow]:
    """One bar per session."""
    rows = []
    for session in node.sessions:
        rows.append(BarRow(
            label=session.display_name,
            data=session,
            category_totals=session.category_totals(),
            fresh_tokens=session.total_input_tokens + session.total_cache_create_tokens,
            cache_tokens=session.total_cache_read_tokens,
            output_tokens=session.total_output_tokens,
        ))
    return rows


def _turn_bar_rows(node: SessionStats) -> list[BarRow]:
    """One bar per turn."""
    rows = []
    for turn in node.turns:
        prefix = "⚡" if turn.after_compact else " "
        rows.append(BarRow(
            label=f"{prefix}T{turn.turn_number:2d}",
            data=turn,
            category_totals=turn.category_breakdown.category_totals(),
            fresh_tokens=turn.input_tokens + turn.cache_create_tokens,
            cache_tokens=turn.cache_read_tokens,
            output_tokens=turn.output_tokens,
        ))
    return rows


def bar_rows_for(node) -> list[BarRow]:
    if isinstance(node, GlobalStats):
        return _session_bar_rows(node)
    if isinstance(node, ProjectStats):
        return _session_bar_rows_for_project(node)
    if isinstance(node, SessionStats):
        return _turn_bar_rows(node)
    return []


def build_chart_bars(rows: list[BarRow], bar_width: int = 28, cursor: int = -1) -> Text:
    """
    Render a list of BarRows as a stacked bar chart.
    Fresh tokens → coloured category segments (█).
    Cache read   → dim░ segments.
    The row at `cursor` is highlighted.
    """
    if not rows:
        return Text("(no data)", style="dim")

    max_total = max(r.fresh_tokens + r.cache_tokens for r in rows) or 1

    result = Text()
    for i, row in enumerate(rows):
        if i > 0:
            result.append("\n")
        row_start = len(result)

        total = row.fresh_tokens + row.cache_tokens
        fresh_chars = max(1, round((row.fresh_tokens / max_total) * bar_width))
        cache_chars = round((row.cache_tokens / max_total) * bar_width)

        cat_totals = row.category_totals
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
        if cache_chars > 0:
            bar.append("░" * cache_chars, style="dim")

        label = row.label[:14].ljust(14)
        result.append(f"{label} {total:8,}  ")
        result.append_text(bar)
        if row.output_tokens:
            result.append(f"  →{row.output_tokens:,}")

        if i == cursor:
            result.stylize("bold on dark_blue", row_start, len(result))

    return result


class BarChart(Static):
    """Focusable stacked bar chart. Up/Down moves cursor; Enter selects the row.

    Subclasses Static so that height: auto correctly sizes to rendered content.
    """

    can_focus = True

    DEFAULT_CSS = """
    BarChart {
        height: auto;
    }
    BarChart:focus {
        border-left: tall $accent;
    }
    """

    BINDINGS = [
        Binding("up",    "cursor_up",   show=False),
        Binding("down",  "cursor_down", show=False),
        Binding("enter", "select_row",  "Open", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._rows: list[BarRow] = []
        self._cursor: int = 0

    def set_rows(self, rows: list[BarRow]) -> None:
        self._rows = rows
        self._cursor = 0
        self.update(build_chart_bars(self._rows, cursor=self._cursor))

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self.update(build_chart_bars(self._rows, cursor=self._cursor))
            self._scroll_into_view()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._rows) - 1:
            self._cursor += 1
            self.update(build_chart_bars(self._rows, cursor=self._cursor))
            self._scroll_into_view()

    def action_select_row(self) -> None:
        from tui.tree import NodeSelected
        if self._rows:
            self.post_message(NodeSelected(self._rows[self._cursor].data, sync_tree=True))

    def _scroll_into_view(self) -> None:
        parent = self.parent
        if hasattr(parent, "scroll_to"):
            parent.scroll_to(y=self._cursor, animate=False)


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
        height: 1fr;
        padding: 0 1 1 1;
    }
    #message-body {
        height: auto;
    }
    """

    def compose(self):
        yield DataTable(id="category-table")
        yield DataTable(id="totals-table")
        with Vertical(id="chart-section"):
            yield Static("", id="chart-legend")
            with VerticalScroll(id="chart-scroll"):
                yield BarChart(id="chart-bars")
        with VerticalScroll(id="message-section"):
            yield Static("", id="message-body")

    def on_mount(self) -> None:
        cat_table = self.query_one("#category-table", DataTable)
        cat_table.add_columns("Category", "Tokens", "%")

        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.add_columns("", "Tokens")

    def _hide_extras(self) -> None:
        self.query_one("#chart-section", Vertical).display = False
        self.query_one("#message-section", VerticalScroll).display = False

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
        if isinstance(node, ProjectStats) and not node.loaded:
            load_project(node)
        self._hide_extras()
        rows, totals = build_rows(node)

        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear()
        for name, tokens, pct in rows:
            cat_table.add_row(name, f"{tokens:,}", f"{pct:.1f}%")

        self._refresh_totals(totals)

        bar_rows = bar_rows_for(node)
        if bar_rows:
            self.query_one("#chart-section", Vertical).display = True
            self.query_one("#chart-legend", Static).update(build_chart_legend())
            self.query_one("#chart-bars", BarChart).set_rows(bar_rows)

    def update_turn(self, turn: TurnStats) -> None:
        """Refresh for a TurnStats node — shows category breakdown and message preview."""
        self._hide_extras()
        rows, totals = build_rows(turn)

        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear()
        for name, tokens, pct in rows:
            cat_table.add_row(name, f"{tokens:,}", f"{pct:.1f}%")

        self._refresh_totals(totals)

        # Show the human message, assistant response, and raw JSON
        msg_scroll = self.query_one("#message-section", VerticalScroll)
        msg_scroll.display = True
        msg_scroll.scroll_home(animate=False)

        content = Text()

        content.append("User\n", style="bold bright_white")
        if turn.user_text:
            content.append(turn.user_text, style="dim")
        else:
            content.append("(tool result — output tokens from previous turn's tool calls)", style="dim italic")

        content.append("\n\nAssistant\n", style="bold bright_white")
        if turn.assistant_text:
            content.append(turn.assistant_text, style="dim")
        else:
            content.append("(no text — assistant made tool calls only)", style="dim italic")

        if turn.tool_calls:
            content.append("\n\nTool calls\n", style="bold bright_white")
            for name, inp in turn.tool_calls:
                content.append(f"  {name}\n", style="bright_yellow")
                for key, val in inp.items():
                    val_str = str(val) if not isinstance(val, str) else val
                    if len(val_str) > 400:
                        val_str = val_str[:400] + "…"
                    val_str = val_str.replace("\n", "\n        ")
                    content.append(f"    {key}: ", style="dim")
                    content.append(f"{val_str}\n", style="white")

        if turn.files_read:
            content.append("\nFiles read\n", style="bold bright_white")
            for path in turn.files_read:
                content.append(f"  {path}\n", style="bright_blue")

        user_json = json.dumps(turn.raw_user, indent=2)
        asst_json = json.dumps(turn.raw_assistant, indent=2)
        user_words = len(user_json.split())
        asst_words = len(asst_json.split())
        content.append("\n\nContent size (word count ≈ token proxy)\n", style="bold bright_white")
        content.append(f"  User message:      {user_words:,} words\n", style="dim")
        content.append(f"  Assistant message: {asst_words:,} words\n", style="dim")

        content.append("\nRaw JSON — user message\n", style="bold bright_white")
        content.append(user_json, style="dim")
        content.append("\n\nRaw JSON — assistant message\n", style="bold bright_white")
        content.append(asst_json, style="dim")

        self.query_one("#message-body", Static).update(content)

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
