from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


CATEGORIES = ["Skills", "Tools", "MCP", "Agents", "Messages", "Reasoning", "Other"]


@dataclass
class CategoryItem:
    name: str
    tokens: int


@dataclass
class CategoryBreakdown:
    skills: list[CategoryItem] = field(default_factory=list)
    tools: list[CategoryItem] = field(default_factory=list)
    mcp_tools: list[CategoryItem] = field(default_factory=list)
    agents: list[CategoryItem] = field(default_factory=list)
    messages_tokens: int = 0
    reasoning_tokens: int = 0
    other_tokens: int = 0

    def category_totals(self) -> dict[str, int]:
        return {
            "Skills":    sum(i.tokens for i in self.skills),
            "Tools":     sum(i.tokens for i in self.tools),
            "MCP":       sum(i.tokens for i in self.mcp_tools),
            "Agents":    sum(i.tokens for i in self.agents),
            "Messages":  self.messages_tokens,
            "Reasoning": self.reasoning_tokens,
            "Other":     self.other_tokens,
        }

    def total_attributed_tokens(self) -> int:
        return sum(self.category_totals().values())


def _merge_breakdowns(breakdowns: list[CategoryBreakdown]) -> CategoryBreakdown:
    merged = CategoryBreakdown()
    for bd in breakdowns:
        merged.skills.extend(bd.skills)
        merged.tools.extend(bd.tools)
        merged.mcp_tools.extend(bd.mcp_tools)
        merged.agents.extend(bd.agents)
        merged.messages_tokens += bd.messages_tokens
        merged.reasoning_tokens += bd.reasoning_tokens
        merged.other_tokens += bd.other_tokens
    return merged


@dataclass
class ExchangeStats:
    exchange_number: int
    timestamp: str
    input_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    output_tokens: int
    category_breakdown: CategoryBreakdown
    after_compact: bool = False
    reasoning_output_tokens: int = 0
    cache_create_5m_tokens: int = 0
    cache_create_1h_tokens: int = 0
    user_text: str = ""
    assistant_text: str = ""
    files_read: list[str] = field(default_factory=list)
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    raw_user: dict = field(default_factory=dict)
    raw_assistants: list[dict] = field(default_factory=list)
    jsonl_path: str = ""
    jsonl_line_start: int = 0
    jsonl_line_end: int = 0
    model: str = ""

    @property
    def first_timestamp(self) -> str:
        return self.raw_user.get("timestamp", "") if self.raw_user else ""

    @property
    def last_timestamp(self) -> str:
        return self.timestamp

    @property
    def duration_seconds(self) -> float:
        t0 = self.first_timestamp
        t1 = self.last_timestamp
        if not t0 or not t1:
            return 0.0
        try:
            dt0 = datetime.fromisoformat(t0.replace("Z", "+00:00"))
            dt1 = datetime.fromisoformat(t1.replace("Z", "+00:00"))
            return (dt1 - dt0).total_seconds()
        except Exception:
            return 0.0


@dataclass
class SessionStats:
    session_id: str
    display_name: str
    exchanges: list[ExchangeStats] = field(default_factory=list)
    jsonl_path: str = ""

    @property
    def first_timestamp(self) -> Optional[str]:
        return self.exchanges[0].timestamp if self.exchanges else None

    @property
    def last_timestamp(self) -> Optional[str]:
        return self.exchanges[-1].last_timestamp if self.exchanges else None

    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.exchanges)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(t.cache_read_tokens for t in self.exchanges)

    @property
    def total_cache_create_tokens(self) -> int:
        return sum(t.cache_create_tokens for t in self.exchanges)

    @property
    def total_cache_create_5m_tokens(self) -> int:
        return sum(t.cache_create_5m_tokens for t in self.exchanges)

    @property
    def total_cache_create_1h_tokens(self) -> int:
        return sum(t.cache_create_1h_tokens for t in self.exchanges)

    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.exchanges)

    @property
    def total_reasoning_output_tokens(self) -> int:
        return sum(t.reasoning_output_tokens for t in self.exchanges)

    def category_totals(self) -> dict[str, int]:
        merged = _merge_breakdowns([t.category_breakdown for t in self.exchanges])
        return merged.category_totals()


@dataclass
class ProjectStats:
    project_slug: str
    display_name: str
    sessions: list[SessionStats] = field(default_factory=list)
    loaded: bool = False
    load_error: Optional[str] = None
    claude_dir: Optional[str] = None  # ~/.claude/projects/<slug> dir, or None for Codex-only
    codex_files: list[str] = field(default_factory=list)  # codex rollout jsonl paths

    @property
    def total_input_tokens(self) -> int:
        return sum(s.total_input_tokens for s in self.sessions)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(s.total_cache_read_tokens for s in self.sessions)

    @property
    def total_cache_create_tokens(self) -> int:
        return sum(s.total_cache_create_tokens for s in self.sessions)

    @property
    def total_cache_create_5m_tokens(self) -> int:
        return sum(s.total_cache_create_5m_tokens for s in self.sessions)

    @property
    def total_cache_create_1h_tokens(self) -> int:
        return sum(s.total_cache_create_1h_tokens for s in self.sessions)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.total_output_tokens for s in self.sessions)

    @property
    def total_reasoning_output_tokens(self) -> int:
        return sum(s.total_reasoning_output_tokens for s in self.sessions)

    def category_totals(self) -> dict[str, int]:
        merged = _merge_breakdowns(
            [_merge_breakdowns([t.category_breakdown for t in s.exchanges])
             for s in self.sessions]
        )
        return merged.category_totals()


@dataclass
class GlobalStats:
    projects: list[ProjectStats] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(p.total_input_tokens for p in self.projects)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(p.total_cache_read_tokens for p in self.projects)

    @property
    def total_cache_create_tokens(self) -> int:
        return sum(p.total_cache_create_tokens for p in self.projects)

    @property
    def total_cache_create_5m_tokens(self) -> int:
        return sum(p.total_cache_create_5m_tokens for p in self.projects)

    @property
    def total_cache_create_1h_tokens(self) -> int:
        return sum(p.total_cache_create_1h_tokens for p in self.projects)

    @property
    def total_output_tokens(self) -> int:
        return sum(p.total_output_tokens for p in self.projects)

    @property
    def total_reasoning_output_tokens(self) -> int:
        return sum(p.total_reasoning_output_tokens for p in self.projects)

    def category_totals(self) -> dict[str, int]:
        all_breakdowns = []
        for p in self.projects:
            for s in p.sessions:
                for t in s.exchanges:
                    all_breakdowns.append(t.category_breakdown)
        return _merge_breakdowns(all_breakdowns).category_totals()
