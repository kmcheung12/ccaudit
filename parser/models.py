from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


CATEGORIES = ["Skills", "Memory", "System Prompt", "Tools", "Agents", "Messages"]


@dataclass
class CategoryItem:
    name: str
    tokens: int


@dataclass
class CategoryBreakdown:
    skills: list[CategoryItem] = field(default_factory=list)
    memory: list[CategoryItem] = field(default_factory=list)
    tools: list[CategoryItem] = field(default_factory=list)
    agents: list[CategoryItem] = field(default_factory=list)
    system_prompt_tokens: int = 0
    messages_tokens: int = 0

    def category_totals(self) -> dict[str, int]:
        return {
            "Skills": sum(i.tokens for i in self.skills),
            "Memory": sum(i.tokens for i in self.memory),
            "System Prompt": self.system_prompt_tokens,
            "Tools": sum(i.tokens for i in self.tools),
            "Agents": sum(i.tokens for i in self.agents),
            "Messages": self.messages_tokens,
        }

    def total_attributed_tokens(self) -> int:
        return sum(self.category_totals().values())


def _merge_breakdowns(breakdowns: list[CategoryBreakdown]) -> CategoryBreakdown:
    merged = CategoryBreakdown()
    for bd in breakdowns:
        merged.skills.extend(bd.skills)
        merged.memory.extend(bd.memory)
        merged.tools.extend(bd.tools)
        merged.agents.extend(bd.agents)
        merged.system_prompt_tokens += bd.system_prompt_tokens
        merged.messages_tokens += bd.messages_tokens
    return merged


@dataclass
class TurnStats:
    turn_number: int
    timestamp: str
    input_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    output_tokens: int
    category_breakdown: CategoryBreakdown
    after_compact: bool = False
    user_text: str = ""       # human-typed portion of the user message
    assistant_text: str = ""  # assistant's text response
    files_read: list[str] = field(default_factory=list)  # file paths accessed via tools
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)  # (tool_name, input_dict)


@dataclass
class SessionStats:
    session_id: str
    display_name: str
    first_timestamp: Optional[str]
    turns: list[TurnStats] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(t.cache_read_tokens for t in self.turns)

    @property
    def total_cache_create_tokens(self) -> int:
        return sum(t.cache_create_tokens for t in self.turns)

    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    def category_totals(self) -> dict[str, int]:
        merged = _merge_breakdowns([t.category_breakdown for t in self.turns])
        return merged.category_totals()


@dataclass
class ProjectStats:
    project_slug: str
    display_name: str
    sessions: list[SessionStats] = field(default_factory=list)
    loaded: bool = False
    load_error: Optional[str] = None

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
    def total_output_tokens(self) -> int:
        return sum(s.total_output_tokens for s in self.sessions)

    def category_totals(self) -> dict[str, int]:
        merged = _merge_breakdowns(
            [_merge_breakdowns([t.category_breakdown for t in s.turns])
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
    def total_output_tokens(self) -> int:
        return sum(p.total_output_tokens for p in self.projects)

    def category_totals(self) -> dict[str, int]:
        all_breakdowns = []
        for p in self.projects:
            for s in p.sessions:
                for t in s.turns:
                    all_breakdowns.append(t.category_breakdown)
        return _merge_breakdowns(all_breakdowns).category_totals()
