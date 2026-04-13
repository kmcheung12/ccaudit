from __future__ import annotations
import json
import unicodedata
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
    cache_create_5m: int
    cache_create_1h: int
    output: int


@dataclass
class BarRow:
    label: str
    data: object          # node data posted as NodeSelected on Enter
    category_totals: dict[str, int]
    fresh_tokens: int     # input + cache_create (bar width basis)
    cache_tokens: int     # cache_read
    output_tokens: int = 0
    input_tokens: int = 0
    cache_create: int = 0


# Category display colours (Rich style strings)
_CAT_STYLE = {
    "Messages": "orange1",
    "Skills":   "bright_yellow",
    "Memory":   "bright_green",
    "Tools":    "bright_blue",
    "MCP":      "bright_red",
    "Agents":   "bright_cyan",
    "Other":    "white",
}


def _get_totals(node) -> TokenTotals:
    """Extract raw token totals from any stats node."""
    if isinstance(node, TurnStats):
        return TokenTotals(
            input_tokens=node.input_tokens,
            cache_read=node.cache_read_tokens,
            cache_create=node.cache_create_tokens,
            cache_create_5m=node.cache_create_5m_tokens,
            cache_create_1h=node.cache_create_1h_tokens,
            output=node.output_tokens,
        )
    return TokenTotals(
        input_tokens=node.total_input_tokens,
        cache_read=node.total_cache_read_tokens,
        cache_create=node.total_cache_create_tokens,
        cache_create_5m=node.total_cache_create_5m_tokens,
        cache_create_1h=node.total_cache_create_1h_tokens,
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
            input_tokens=project.total_input_tokens,
            cache_create=project.total_cache_create_tokens,
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
            input_tokens=session.total_input_tokens,
            cache_create=session.total_cache_create_tokens,
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
            input_tokens=turn.input_tokens,
            cache_create=turn.cache_create_tokens,
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


_LABEL_W = 14
_NUM_W = 12


def _display_width(s: str) -> int:
    """Return the terminal display width of a string (wide chars count as 2)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in s)


def _ljust_display(s: str, width: int) -> str:
    """Like str.ljust but pads to visual display width, not character count."""
    return s + " " * max(0, width - _display_width(s))


def build_chart_bars(rows: list[BarRow], bar_width: int = 28, cursor: int = -1) -> Text:
    """
    Render a list of BarRows as a stacked bar chart with columnar token counts.

    Layout per row:
        <label>  <category bar (fresh tokens only)>  Input  Write  Read  Out
    """
    if not rows:
        return Text("(no data)", style="dim")

    max_fresh = max(r.fresh_tokens for r in rows) or 1

    result = Text()
    # Header — leading "  " matches the per-row number prefix so columns align
    result.append(" " * _LABEL_W + "  " + " " * bar_width)
    result.append(
        f"  {'Input':>{_NUM_W}}  {'Cache Write':>{_NUM_W}}  {'Cache Read':>{_NUM_W}}  {'Out':>{_NUM_W}}",
        style="dim",
    )

    for i, row in enumerate(rows):
        result.append("\n")
        row_start = len(result)

        fresh_chars = max(1, round((row.fresh_tokens / max_fresh) * bar_width))
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
        # Pad to uniform bar_width so number columns stay aligned
        bar.append(" " * (bar_width - fresh_chars))

        label = _ljust_display(row.label[:_LABEL_W], _LABEL_W)
        result.append(f"{label}  ")
        result.append_text(bar)
        result.append(
            f"  {row.input_tokens:>{_NUM_W},}"
            f"  {row.cache_create:>{_NUM_W},}"
            f"  {row.cache_tokens:>{_NUM_W},}"
            f"  {row.output_tokens:>{_NUM_W},}"
        )

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
    #turn-path {
        display: none;
        padding: 0 1;
        height: auto;
        color: $text-muted;
    }
    #session-times {
        display: none;
        padding: 0 1;
        height: auto;
        color: $text-muted;
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
        yield Static("", id="turn-path")
        yield Static("", id="session-times")
        yield DataTable(id="category-table")
        yield DataTable(id="totals-table")
        with Vertical(id="chart-section"):
            yield Static("", id="chart-legend")
            with VerticalScroll(id="chart-scroll"):
                yield BarChart(id="chart-bars")
        with VerticalScroll(id="message-section"):
            yield Static("", id="message-body")

    def on_mount(self) -> None:
        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.add_columns("", "Tokens")

    def _refresh_category_table(self, cat_totals: dict) -> None:
        total = sum(cat_totals.values()) or 1
        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear(columns=True)
        for cat in CATEGORIES:
            header = Text()
            header.append("█ ", style=_CAT_STYLE.get(cat, "white"))
            header.append(cat)
            cat_table.add_column(header)
        cat_table.add_row(*[f"{cat_totals.get(cat, 0):,}" for cat in CATEGORIES])
        cat_table.add_row(*[f"{cat_totals.get(cat, 0) / total * 100:.1f}%" for cat in CATEGORIES])

    def _hide_extras(self) -> None:
        self.query_one("#turn-path", Static).display = False
        self.query_one("#session-times", Static).display = False
        self.query_one("#chart-section", Vertical).display = False
        self.query_one("#message-section", VerticalScroll).display = False

    def _refresh_totals(self, totals: TokenTotals) -> None:
        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.clear()
        total_in = totals.input_tokens + totals.cache_read + totals.cache_create
        cache_pct = (totals.cache_read / total_in * 100) if total_in > 0 else 0.0
        totals_table.add_row("Input (fresh)", f"{totals.input_tokens:,}")
        totals_table.add_row("Cache write",   f"{totals.cache_create:,}")
        totals_table.add_row("  5 min",       f"{totals.cache_create_5m:,}")
        totals_table.add_row("  1 hour",      f"{totals.cache_create_1h:,}")
        totals_table.add_row("Cache read",    f"{totals.cache_read:,}  ({cache_pct:.0f}% hit)")
        totals_table.add_row("Output",        f"{totals.output:,}")

    def update(self, node) -> None:
        """Refresh for GlobalStats, ProjectStats, or SessionStats."""
        if isinstance(node, ProjectStats) and not node.loaded:
            load_project(node)
        self._hide_extras()

        times_widget = self.query_one("#session-times", Static)
        if isinstance(node, SessionStats) and node.turns:
            start = node.first_timestamp[:19].replace("T", " ") if node.first_timestamp else "?"
            end = node.turns[-1].timestamp[:19].replace("T", " ") if node.turns[-1].timestamp else "?"
            times_widget.update(f"Start: {start}   End: {end}")
            times_widget.display = True
        else:
            times_widget.display = False

        if isinstance(node, TurnStats):
            cat_totals = node.category_breakdown.category_totals()
        else:
            cat_totals = node.category_totals()
        self._refresh_category_table(cat_totals)
        self._refresh_totals(_get_totals(node))

        bar_rows = bar_rows_for(node)
        if bar_rows:
            self.query_one("#chart-section", Vertical).display = True
            self.query_one("#chart-legend", Static).update(build_chart_legend())
            self.query_one("#chart-bars", BarChart).set_rows(bar_rows)

    def update_turn(self, turn: TurnStats) -> None:
        """Refresh for a TurnStats node — shows category breakdown and message preview."""
        self._hide_extras()
        path_widget = self.query_one("#turn-path", Static)
        if turn.jsonl_path:
            path_widget.update(turn.jsonl_path)
            path_widget.display = True
        self._refresh_category_table(turn.category_breakdown.category_totals())
        self._refresh_totals(_get_totals(turn))

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
        asst_jsons = [json.dumps(m, indent=2) for m in turn.raw_assistants]
        user_words = len(user_json.split())
        asst_words = sum(len(j.split()) for j in asst_jsons)
        content.append("\n\nContent size (word count ≈ token proxy)\n", style="bold bright_white")
        content.append(f"  User message:      {user_words:,} words\n", style="dim")
        content.append(f"  Assistant messages ({len(asst_jsons)}): {asst_words:,} words\n", style="dim")

        content.append("\nRaw JSON — user message\n", style="bold bright_white")
        content.append(user_json, style="dim")
        for idx, asst_json in enumerate(asst_jsons, start=1):
            label = f"\n\nRaw JSON — assistant message {idx}\n" if len(asst_jsons) > 1 else "\n\nRaw JSON — assistant message\n"
            content.append(label, style="bold bright_white")
            content.append(asst_json, style="dim")

        self.query_one("#message-body", Static).update(content)

    def update_category(self, turn: TurnStats, cat_name: str) -> None:
        """Show individual items within a category for a turn."""
        self._hide_extras()
        rows = build_category_rows(turn, cat_name)

        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear(columns=True)
        col_header = Text()
        col_header.append("█ ", style=_CAT_STYLE.get(cat_name, "white"))
        col_header.append(cat_name)
        cat_table.add_column(col_header)
        cat_table.add_column("Tokens")
        cat_table.add_column("%")
        if not rows:
            cat_table.add_row(f"(no items in {cat_name})", "", "")
        else:
            total = sum(t for _, t in rows)
            for name, tokens in rows:
                pct = (tokens / total * 100) if total > 0 else 0.0
                cat_table.add_row(name, f"{tokens:,}", f"{pct:.1f}%")

        self._refresh_totals(_get_totals(turn))
