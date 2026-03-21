# ccaudit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an interactive Textual TUI that reads `~/.claude/projects/*.jsonl` files and lets you drill down from global → project → session → turn → category → item to see token usage at every level.

**Architecture:** Parser layer (loader + categorizer) builds a typed stats tree from JSONL; TUI layer binds that tree into a Textual split-pane app with a collapsible tree on the left and a DataTable detail panel on the right. Parsing is lazy (per-project, on first expand).

**Tech Stack:** Python ≥ 3.11, [Textual](https://github.com/Textualize/textual), pytest

> **⚠ Important — always run from the project root.** The package directory is named `parser/`, which collides with Python's stdlib `parser` module. All `python` and `pytest` commands must be run from the `ccaudit/` project root so that the local `parser/` package takes precedence on `sys.path`. Never install this as a package outside its directory without renaming the package first.

---

## File Map

| File | Responsibility |
|---|---|
| `requirements.txt` | `textual` + `pytest` dev dependency |
| `parser/__init__.py` | empty package marker |
| `parser/models.py` | Dataclasses: `CategoryItem`, `CategoryBreakdown`, `TurnStats`, `SessionStats`, `ProjectStats`, `GlobalStats` with aggregation properties |
| `parser/loader.py` | Scans `~/.claude/projects/`, parses `.jsonl` files, builds `SessionStats` with `TurnStats` list |
| `parser/categorizer.py` | Heuristic text scanning → category buckets → proportional token attribution |
| `tui/__init__.py` | empty package marker |
| `tui/detail.py` | Right pane: renders a `DataTable` for any stats node (category breakdown + token totals) |
| `tui/tree.py` | Left pane: builds Textual `Tree` from `GlobalStats`, binds nodes to stats objects, handles lazy expand |
| `tui/app.py` | `CCAuditApp(App)`: layout, keyboard bindings (`q`, `/`, `Escape`), wires tree selection → detail pane |
| `main.py` | Entry point: `python main.py` (run from project root — see note below) |
| `tests/test_models.py` | Unit tests for aggregation logic on `CategoryBreakdown`, `SessionStats`, `ProjectStats`, `GlobalStats` |
| `tests/test_loader.py` | Unit tests for `slug_to_display`, `load_session` (using tmp JSONL fixtures) |
| `tests/test_categorizer.py` | Unit tests for each category detection pattern and proportional attribution |
| `tests/test_detail.py` | Unit tests for detail pane row generation logic (pure function, no TUI) |

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `parser/__init__.py`
- Create: `tui/__init__.py`
- Create: `tests/__init__.py`
- Create: `pytest.ini`

- [ ] **Step 1: Create `requirements.txt`**

```
textual>=0.80.0
pytest>=8.0
```

- [ ] **Step 2: Create empty package markers**

```bash
mkdir -p parser tui tests
touch parser/__init__.py tui/__init__.py tests/__init__.py
```

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 4: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: no errors, `textual` importable.

- [ ] **Step 5: Verify pytest runs (zero tests is fine)**

```bash
pytest -v
```

Expected: `no tests ran` or similar with exit 0.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt parser/__init__.py tui/__init__.py tests/__init__.py pytest.ini
git commit -m "chore: project scaffold"
```

---

## Task 2: Data Models

**Files:**
- Create: `parser/models.py`
- Create: `tests/test_models.py`

These are pure dataclasses — no I/O, no TUI. All aggregation logic lives here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_models.py
from parser.models import (
    CategoryItem, CategoryBreakdown, TurnStats, SessionStats,
    ProjectStats, GlobalStats,
)


def make_breakdown(skills_tokens=0, memory_tokens=0, tools_tokens=0,
                   agents_tokens=0, system_tokens=0, messages_tokens=0):
    bd = CategoryBreakdown()
    if skills_tokens:
        bd.skills.append(CategoryItem(name="TestSkill", tokens=skills_tokens))
    if memory_tokens:
        bd.memory.append(CategoryItem(name="mem1", tokens=memory_tokens))
    if tools_tokens:
        bd.tools.append(CategoryItem(name="tool1", tokens=tools_tokens))
    if agents_tokens:
        bd.agents.append(CategoryItem(name="Agent", tokens=agents_tokens))
    bd.system_prompt_tokens = system_tokens
    bd.messages_tokens = messages_tokens
    return bd


def make_turn(input_t=100, cache_read=500, cache_create=200, output=50, breakdown=None):
    if breakdown is None:
        breakdown = make_breakdown(messages_tokens=100)
    return TurnStats(
        turn_number=1,
        timestamp="2026-01-01T00:00:00Z",
        input_tokens=input_t,
        cache_read_tokens=cache_read,
        cache_create_tokens=cache_create,
        output_tokens=output,
        category_breakdown=breakdown,
    )


# CategoryBreakdown tests

def test_breakdown_category_totals():
    bd = make_breakdown(skills_tokens=100, memory_tokens=50, messages_tokens=200)
    totals = bd.category_totals()
    assert totals["Skills"] == 100
    assert totals["Memory"] == 50
    assert totals["Messages"] == 200
    assert totals["Tools"] == 0
    assert totals["Agents"] == 0
    assert totals["System Prompt"] == 0


def test_breakdown_total_input_tokens():
    bd = make_breakdown(skills_tokens=100, memory_tokens=50, system_tokens=9550, messages_tokens=200)
    assert bd.total_attributed_tokens() == 9900


# TurnStats tests

def test_turn_aggregates():
    turn = make_turn(input_t=100, cache_read=500, cache_create=200, output=50)
    assert turn.input_tokens == 100
    assert turn.cache_read_tokens == 500
    assert turn.cache_create_tokens == 200
    assert turn.output_tokens == 50


# SessionStats tests

def test_session_aggregates_across_turns():
    session = SessionStats(session_id="abc", display_name="abc1234", first_timestamp=None)
    session.turns.append(make_turn(input_t=100, cache_read=500, cache_create=200, output=50,
                                   breakdown=make_breakdown(messages_tokens=100)))
    session.turns.append(make_turn(input_t=200, cache_read=700, cache_create=300, output=80,
                                   breakdown=make_breakdown(skills_tokens=150, messages_tokens=50)))
    assert session.total_input_tokens == 300
    assert session.total_cache_read_tokens == 1200
    assert session.total_cache_create_tokens == 500
    assert session.total_output_tokens == 130
    totals = session.category_totals()
    assert totals["Skills"] == 150
    assert totals["Messages"] == 150


def test_session_with_no_turns():
    session = SessionStats(session_id="xyz", display_name="xyz1234", first_timestamp=None)
    assert session.total_input_tokens == 0
    assert session.category_totals()["Messages"] == 0


# ProjectStats tests

def test_project_aggregates_across_sessions():
    project = ProjectStats(project_slug="test-proj", display_name="proj")
    s1 = SessionStats(session_id="s1", display_name="s1abc", first_timestamp=None)
    s1.turns.append(make_turn(input_t=100, breakdown=make_breakdown(messages_tokens=100)))
    s2 = SessionStats(session_id="s2", display_name="s2abc", first_timestamp=None)
    s2.turns.append(make_turn(input_t=200, breakdown=make_breakdown(skills_tokens=200)))
    project.sessions = [s1, s2]
    assert project.total_input_tokens == 300
    totals = project.category_totals()
    assert totals["Skills"] == 200
    assert totals["Messages"] == 100


# GlobalStats tests

def test_global_aggregates_across_projects():
    g = GlobalStats()
    p1 = ProjectStats(project_slug="p1", display_name="p1")
    s1 = SessionStats(session_id="s1", display_name="s1abc", first_timestamp=None)
    s1.turns.append(make_turn(input_t=100, output=10, breakdown=make_breakdown(messages_tokens=100)))
    p1.sessions = [s1]

    p2 = ProjectStats(project_slug="p2", display_name="p2")
    s2 = SessionStats(session_id="s2", display_name="s2abc", first_timestamp=None)
    s2.turns.append(make_turn(input_t=300, output=30, breakdown=make_breakdown(skills_tokens=300)))
    p2.sessions = [s2]

    g.projects = [p1, p2]
    assert g.total_input_tokens == 400
    assert g.total_output_tokens == 40
    totals = g.category_totals()
    assert totals["Skills"] == 300
    assert totals["Messages"] == 100
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_models.py -v
```

Expected: `ImportError` — models module does not exist yet.

- [ ] **Step 3: Implement `parser/models.py`**

```python
# parser/models.py
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
        merged = _merge_breakdowns(
            [_merge_breakdowns(
                [_merge_breakdowns([t.category_breakdown for t in s.turns])
                 for s in p.sessions]
             ) for p in self.projects]
        )
        return merged.category_totals()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_models.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add parser/models.py tests/test_models.py
git commit -m "feat: data models with aggregation"
```

---

## Task 3: JSONL Loader

**Files:**
- Create: `parser/loader.py`
- Create: `tests/test_loader.py`

The loader turns raw `.jsonl` files into `SessionStats`. Key logic: pair each `assistant` message (with its `usage` field) to the immediately preceding `user` message content; extract `compact_boundary` positions.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_loader.py
import json
import pytest
from pathlib import Path
from parser.loader import slug_to_display, load_session, list_projects
from parser.models import ProjectStats


def write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines))


# slug_to_display

def test_slug_to_display_simple():
    assert slug_to_display("-Users-alan-code-feedr") == "feedr"


def test_slug_to_display_hyphenated_project():
    assert slug_to_display("-Users-alan-code-my-project") == "my-project"


def test_slug_to_display_short():
    assert slug_to_display("proj") == "proj"


# load_session basic

def test_load_session_empty_file(tmp_path):
    f = tmp_path / "abc123.jsonl"
    f.write_text("")
    session = load_session(f)
    assert session.session_id == "abc123"
    assert session.turns == []


def test_load_session_skips_malformed_lines(tmp_path):
    f = tmp_path / "abc123.jsonl"
    f.write_text("not json\n{also bad\n")
    session = load_session(f)
    assert session.turns == []


def test_load_session_extracts_turns(tmp_path):
    f = tmp_path / "sess1.jsonl"
    write_jsonl(f, [
        {
            "type": "user",
            "message": {"role": "user", "content": "Hello world, this is a test message"},
            "timestamp": "2026-01-01T00:00:00Z",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": 5,
                    "cache_read_input_tokens": 9550,
                    "cache_creation_input_tokens": 100,
                    "output_tokens": 20,
                },
            },
            "timestamp": "2026-01-01T00:00:01Z",
        },
    ])
    session = load_session(f)
    assert len(session.turns) == 1
    turn = session.turns[0]
    assert turn.input_tokens == 5
    assert turn.cache_read_tokens == 9550
    assert turn.cache_create_tokens == 100
    assert turn.output_tokens == 20
    assert turn.turn_number == 1


def test_load_session_display_name(tmp_path):
    f = tmp_path / "4b177c76-c056-4ed7-b62f-1d41710cf376.jsonl"
    f.write_text("")
    session = load_session(f)
    assert session.display_name == "4b177c76"


def test_load_session_multiple_turns(tmp_path):
    f = tmp_path / "multi.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "msg1"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50, "output_tokens": 5}}, "timestamp": "2026-01-01T00:00:01Z"},
        {"type": "user", "message": {"content": "msg2"}, "timestamp": "2026-01-01T00:00:02Z"},
        {"type": "assistant", "message": {"usage": {"input_tokens": 8, "cache_read_input_tokens": 200, "cache_creation_input_tokens": 30, "output_tokens": 12}}, "timestamp": "2026-01-01T00:00:03Z"},
    ])
    session = load_session(f)
    assert len(session.turns) == 2
    assert session.turns[0].turn_number == 1
    assert session.turns[1].turn_number == 2


def test_load_session_marks_after_compact(tmp_path):
    f = tmp_path / "compact.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "before"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50, "output_tokens": 5}}, "timestamp": "2026-01-01T00:00:01Z"},
        {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "compactMetadata": {"trigger": "auto", "preTokens": 5000}, "timestamp": "2026-01-01T00:00:02Z"},
        {"type": "user", "message": {"content": "after compact"}, "timestamp": "2026-01-01T00:00:03Z"},
        {"type": "assistant", "message": {"usage": {"input_tokens": 3, "cache_read_input_tokens": 200, "cache_creation_input_tokens": 20, "output_tokens": 10}}, "timestamp": "2026-01-01T00:00:04Z"},
    ])
    session = load_session(f)
    assert len(session.turns) == 2
    assert not session.turns[0].after_compact
    assert session.turns[1].after_compact


def test_load_session_skips_assistant_without_usage(tmp_path):
    f = tmp_path / "nousage.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "hi"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant"}, "timestamp": "2026-01-01T00:00:01Z"},
    ])
    session = load_session(f)
    assert session.turns == []


# list_projects

def test_list_projects(tmp_path):
    (tmp_path / "-Users-alice-code-alpha").mkdir()
    (tmp_path / "-Users-alice-code-beta").mkdir()
    projects = list_projects(tmp_path)
    names = [p.display_name for p in projects]
    assert "alpha" in names
    assert "beta" in names
    for p in projects:
        assert not p.loaded
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_loader.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `parser/loader.py`**

```python
# parser/loader.py
from __future__ import annotations
import json
from pathlib import Path
from parser.models import ProjectStats, SessionStats, TurnStats, CategoryBreakdown
from parser import categorizer

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def slug_to_display(slug: str) -> str:
    """Convert -Users-alan-code-my-project to my-project."""
    # Strip leading dash, split on dash, rejoin everything after the known path segments
    # Pattern: -Users-<user>-<...>-<project-name>
    # We want everything from the 4th segment onward (index 3+)
    parts = slug.lstrip("-").split("-")
    if len(parts) >= 4:
        return "-".join(parts[3:])
    return slug


def list_projects(projects_dir: Path = PROJECTS_DIR) -> list[ProjectStats]:
    """Return unloaded ProjectStats for each subdirectory."""
    projects = []
    for d in sorted(projects_dir.iterdir()):
        if d.is_dir():
            projects.append(ProjectStats(
                project_slug=d.name,
                display_name=slug_to_display(d.name),
            ))
    return projects


def load_project(project: ProjectStats, projects_dir: Path = PROJECTS_DIR) -> None:
    """Load all sessions for a project in-place. Sets project.loaded = True."""
    try:
        project_dir = projects_dir / project.project_slug
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            project.sessions.append(load_session(jsonl_file))
        project.loaded = True
    except Exception as e:
        project.load_error = str(e)
        project.loaded = True


def _extract_text(content) -> str:
    """Flatten message content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        for ib in inner:
                            if isinstance(ib, dict) and ib.get("type") == "text":
                                parts.append(ib.get("text", ""))
                    else:
                        parts.append(str(inner))
        return "\n".join(parts)
    return ""


def load_session(jsonl_file: Path) -> SessionStats:
    """Parse a .jsonl file into a SessionStats."""
    session_id = jsonl_file.stem
    display_name = session_id[:8]

    raw_messages: list[dict] = []
    with open(jsonl_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw_messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Determine positions after compact boundaries
    compact_positions: set[int] = set()
    in_compact = False
    for i, msg in enumerate(raw_messages):
        if msg.get("type") == "system" and msg.get("subtype") == "compact_boundary":
            in_compact = True
        if in_compact and msg.get("type") == "user":
            compact_positions.add(i)
            in_compact = False

    # Determine system prompt baseline from first assistant message
    system_prompt_tokens = 0
    for msg in raw_messages:
        if msg.get("type") == "assistant":
            usage = msg.get("message", {}).get("usage", {})
            if usage:
                system_prompt_tokens = usage.get("cache_read_input_tokens", 0)
                break

    # Build turns: pair each user message with the next assistant message
    turns: list[TurnStats] = []
    pending_user_text: str | None = None
    pending_user_idx: int = -1
    after_compact = False
    turn_number = 0

    for i, msg in enumerate(raw_messages):
        msg_type = msg.get("type")

        if msg_type == "user":
            content = msg.get("message", {}).get("content", "")
            pending_user_text = _extract_text(content)
            pending_user_idx = i
            after_compact = i in compact_positions

        elif msg_type == "assistant":
            usage = msg.get("message", {}).get("usage")
            if not usage:
                continue

            input_tokens = usage.get("input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)

            text = pending_user_text or ""
            breakdown = categorizer.categorize(
                text=text,
                input_tokens=input_tokens,
                system_prompt_tokens=system_prompt_tokens,
            )

            turn_number += 1
            turns.append(TurnStats(
                turn_number=turn_number,
                timestamp=msg.get("timestamp", ""),
                input_tokens=input_tokens,
                cache_read_tokens=cache_read,
                cache_create_tokens=cache_create,
                output_tokens=output_tokens,
                category_breakdown=breakdown,
                after_compact=after_compact,
            ))
            pending_user_text = None
            after_compact = False

    first_timestamp = None
    for msg in raw_messages:
        if "timestamp" in msg:
            first_timestamp = msg["timestamp"]
            break

    return SessionStats(
        session_id=session_id,
        display_name=display_name,
        first_timestamp=first_timestamp,
        turns=turns,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_loader.py -v
```

Note: `test_load_session_extracts_turns` may fail until categorizer exists. It's fine to stub categorizer first (see below) — the loader imports it. Add this stub in `parser/categorizer.py` before running:

```python
# parser/categorizer.py (temporary stub — will be replaced in Task 4)
from parser.models import CategoryBreakdown

def categorize(text: str, input_tokens: int, system_prompt_tokens: int = 0) -> CategoryBreakdown:
    bd = CategoryBreakdown()
    bd.messages_tokens = input_tokens
    return bd
```

Then run:

```bash
pytest tests/test_loader.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add parser/loader.py parser/categorizer.py tests/test_loader.py
git commit -m "feat: JSONL loader with session and turn parsing"
```

---

## Task 4: Categorizer

**Files:**
- Modify: `parser/categorizer.py` (replace stub with full implementation)
- Create: `tests/test_categorizer.py`

The categorizer scans the flattened user message text for category markers, builds character-count buckets, then scales each bucket proportionally to `input_tokens`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_categorizer.py
from parser.categorizer import categorize, extract_categories


SKILL_BLOCK = """Base directory: /Users/alan/.claude/plugins/cache/superpowers/5.0.5/skills/brainstorming

# Brainstorming Ideas Into Designs

Help turn ideas into fully formed designs.
"""

MEMORY_BLOCK = """---
name: user_role
description: user is a senior engineer
type: user
---

User is a senior engineer working on Python tools.
"""

SYSTEM_REMINDER_BLOCK = """<system-reminder>
Today's date is 2026-03-21.
</system-reminder>"""

FUNCTION_RESULTS_BLOCK = """<function_results>
{"output": "some tool output here"}
</function_results>"""


def test_skill_detected():
    cats = extract_categories(SKILL_BLOCK)
    assert "Skills" in cats
    items = cats["Skills"]
    assert len(items) == 1
    assert items[0].name == "Brainstorming Ideas Into Designs"


def test_memory_detected():
    cats = extract_categories(MEMORY_BLOCK)
    assert "Memory" in cats
    items = cats["Memory"]
    assert len(items) == 1
    assert items[0].name == "user_role"


def test_system_reminder_detected():
    cats = extract_categories(SYSTEM_REMINDER_BLOCK)
    assert "Tools" in cats
    assert len(cats["Tools"]) == 1


def test_function_results_detected():
    cats = extract_categories(FUNCTION_RESULTS_BLOCK)
    assert "Tools" in cats


def test_unmatched_text_goes_to_messages():
    text = "Just a plain user message with no special markers."
    cats = extract_categories(text)
    assert "Messages" in cats
    assert cats["Messages"][0].name == "Messages"


def test_proportional_attribution_sums_to_input_tokens():
    text = SKILL_BLOCK + "\n" + MEMORY_BLOCK + "\n" + "plain message text"
    bd = categorize(text=text, input_tokens=1000, system_prompt_tokens=0)
    total = bd.total_attributed_tokens()
    assert total == 1000


def test_proportional_attribution_with_system_prompt():
    # system prompt tokens are passed in directly, not from text
    text = "short user message"
    bd = categorize(text=text, input_tokens=50, system_prompt_tokens=9550)
    # system prompt should be exactly 9550
    assert bd.system_prompt_tokens == 9550
    # messages gets the proportional share of input_tokens
    assert bd.messages_tokens == 50


def test_empty_text_produces_zero_tokens():
    bd = categorize(text="", input_tokens=100, system_prompt_tokens=0)
    assert bd.total_attributed_tokens() == 100  # all goes to messages (fallback)


def test_multiple_skills_detected():
    two_skills = SKILL_BLOCK + "\n---SEPARATOR---\n" + """Base directory: /Users/alan/.claude/plugins/cache/superpowers/5.0.5/skills/tdd

# Test-Driven Development

Write tests first.
"""
    cats = extract_categories(two_skills)
    assert len(cats["Skills"]) == 2
    names = [i.name for i in cats["Skills"]]
    assert "Brainstorming Ideas Into Designs" in names
    assert "Test-Driven Development" in names


def test_first_match_wins_for_ambiguous_blocks():
    # A block that matches Skills first should not also match Messages
    cats = extract_categories(SKILL_BLOCK)
    messages_items = cats.get("Messages", [])
    # The skill text should not appear in Messages
    for item in messages_items:
        assert "Brainstorming" not in item.name
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_categorizer.py -v
```

Expected: failures (stub categorizer doesn't implement detection).

- [ ] **Step 3: Implement `parser/categorizer.py`**

```python
# parser/categorizer.py
from __future__ import annotations
import re
from parser.models import CategoryBreakdown, CategoryItem

# Regex patterns for category detection
_SKILL_PATTERN = re.compile(
    r"Base directory: /[^\n]*/skills/[^\n]*\n+#\s+([^\n]+)",
    re.MULTILINE,
)
_MEMORY_PATTERN = re.compile(
    r"---\s*\nname:\s*(\S+)[^\n]*\n(?:.*\n)*?---",
    re.MULTILINE,
)
_SYSTEM_REMINDER_PATTERN = re.compile(
    r"<system-reminder>.*?</system-reminder>",
    re.DOTALL,
)
_FUNCTION_RESULTS_PATTERN = re.compile(
    r"<function_results>.*?</function_results>",
    re.DOTALL,
)
_AGENT_PATTERN = re.compile(
    r'"name":\s*"Agent"',
)


def extract_categories(text: str) -> dict[str, list[CategoryItem]]:
    """
    Scan text top-to-bottom, attribute each region to a category.
    Returns dict of category name → list of CategoryItem (with char counts as tokens placeholder).
    Items use char_count as tokens (caller scales proportionally).
    """
    # We track which character ranges are consumed to avoid double-counting.
    consumed = bytearray(len(text))  # 0 = free, 1 = consumed

    categories: dict[str, list[CategoryItem]] = {
        "Skills": [], "Memory": [], "Tools": [], "Agents": [], "Messages": [],
    }

    def consume(start: int, end: int, category: str, name: str) -> None:
        char_count = end - start
        for i in range(start, min(end, len(consumed))):
            consumed[i] = 1
        categories[category].append(CategoryItem(name=name, tokens=char_count))

    # Skills: Base directory: /.../skills/... followed by # Heading
    for m in _SKILL_PATTERN.finditer(text):
        block_start = m.start()
        # Extend the block to the next skill marker or end
        next_match = _SKILL_PATTERN.search(text, m.end())
        block_end = next_match.start() if next_match else len(text)
        # Find the actual end of this skill block (before next Base directory:)
        next_base = text.find("\nBase directory:", m.end())
        if next_base != -1:
            block_end = next_base
        skill_name = m.group(1).strip()
        consume(block_start, block_end, "Skills", skill_name)

    # Memory: YAML frontmatter --- name: ... ---
    for m in _MEMORY_PATTERN.finditer(text):
        if any(consumed[m.start():m.end()]):
            continue
        name_match = re.search(r"name:\s*(\S+)", m.group(0))
        name = name_match.group(1) if name_match else "memory"
        consume(m.start(), m.end(), "Memory", name)

    # Tools: <system-reminder> and <function_results>
    for pattern in (_SYSTEM_REMINDER_PATTERN, _FUNCTION_RESULTS_PATTERN):
        for m in pattern.finditer(text):
            if any(consumed[m.start():m.end()]):
                continue
            tag = "system-reminder" if "system-reminder" in m.group(0) else "tool-result"
            consume(m.start(), m.end(), "Tools", tag)

    # Agents: blocks containing "name": "Agent"
    for m in _AGENT_PATTERN.finditer(text):
        # Find surrounding block (crude: find enclosing braces)
        start = text.rfind("{", 0, m.start())
        end = text.find("}", m.end()) + 1
        if start == -1 or end == 0:
            continue
        if any(consumed[start:end]):
            continue
        consume(start, end, "Agents", "Agent")

    # Messages: everything not yet consumed
    remaining = "".join(
        ch for i, ch in enumerate(text) if not consumed[i]
    )
    if remaining.strip():
        categories["Messages"].append(CategoryItem(name="Messages", tokens=len(remaining)))

    return categories


def categorize(text: str, input_tokens: int, system_prompt_tokens: int = 0) -> CategoryBreakdown:
    """
    Build a CategoryBreakdown with tokens proportionally attributed from input_tokens.
    system_prompt_tokens is set directly (from first-turn cache_read baseline).
    """
    bd = CategoryBreakdown()
    bd.system_prompt_tokens = system_prompt_tokens

    if not text.strip():
        bd.messages_tokens = input_tokens
        return bd

    cats = extract_categories(text)
    total_chars = sum(
        item.tokens  # here tokens = char_count placeholder
        for items in cats.values()
        for item in items
    )

    if total_chars == 0:
        bd.messages_tokens = input_tokens
        return bd

    def scale(char_count: int) -> int:
        return round((char_count / total_chars) * input_tokens)

    for item in cats["Skills"]:
        bd.skills.append(CategoryItem(name=item.name, tokens=scale(item.tokens)))
    for item in cats["Memory"]:
        bd.memory.append(CategoryItem(name=item.name, tokens=scale(item.tokens)))
    for item in cats["Tools"]:
        bd.tools.append(CategoryItem(name=item.name, tokens=scale(item.tokens)))
    for item in cats["Agents"]:
        bd.agents.append(CategoryItem(name=item.name, tokens=scale(item.tokens)))

    messages_chars = sum(item.tokens for item in cats.get("Messages", []))
    bd.messages_tokens = scale(messages_chars)

    # Fix rounding drift: ensure sum == input_tokens
    attributed = bd.total_attributed_tokens() - bd.system_prompt_tokens
    drift = input_tokens - attributed
    bd.messages_tokens += drift

    return bd
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_categorizer.py tests/test_loader.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add parser/categorizer.py tests/test_categorizer.py
git commit -m "feat: category detection with proportional token attribution"
```

---

## Task 5: Detail Pane Logic

**Files:**
- Create: `tui/detail.py`
- Create: `tests/test_detail.py`

The detail pane renders a DataTable. Extract the row-generation logic as a pure function so it's testable without spinning up a Textual app.

- [ ] **Step 1: Write the failing tests**

> Note: `tui/detail.py` imports Textual at module level, so `textual` must be installed (Task 1) before running these tests. The `build_rows` and `build_category_rows` functions are pure logic and do not require a running app.

```python
# tests/test_detail.py
from parser.models import (
    CategoryBreakdown, CategoryItem, TurnStats, SessionStats, ProjectStats, GlobalStats,
)
from tui.detail import build_rows, build_category_rows, TokenTotals


def make_breakdown(skills=0, memory=0, tools=0, agents=0, system=0, messages=0):
    bd = CategoryBreakdown()
    if skills:
        bd.skills.append(CategoryItem("TestSkill", skills))
    if memory:
        bd.memory.append(CategoryItem("mem1", memory))
    if tools:
        bd.tools.append(CategoryItem("tool1", tools))
    if agents:
        bd.agents.append(CategoryItem("Agent", agents))
    bd.system_prompt_tokens = system
    bd.messages_tokens = messages
    return bd


def make_turn(input_t=1000, cache_read=9550, cache_create=500, output=100, breakdown=None):
    if breakdown is None:
        breakdown = make_breakdown(messages=1000)
    return TurnStats(
        turn_number=1, timestamp="2026-01-01T00:00:00Z",
        input_tokens=input_t, cache_read_tokens=cache_read,
        cache_create_tokens=cache_create, output_tokens=output,
        category_breakdown=breakdown,
    )


def test_build_rows_for_turn():
    bd = make_breakdown(skills=200, memory=100, system=9550, messages=700)
    turn = make_turn(input_t=1000, cache_read=9550, cache_create=500, output=100, breakdown=bd)
    rows, totals = build_rows(turn)
    categories = {r[0]: r for r in rows}
    assert "Skills" in categories
    assert categories["Skills"][1] == 200  # tokens
    assert "Messages" in categories
    assert totals.input_tokens == 1000
    assert totals.cache_read == 9550
    assert totals.output == 100


def test_build_rows_percentages_sum_to_100():
    bd = make_breakdown(skills=300, memory=200, messages=500)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows, _ = build_rows(turn)
    total_pct = sum(r[2] for r in rows)
    assert abs(total_pct - 100.0) < 1.0  # allow rounding


def test_build_rows_excludes_zero_categories():
    bd = make_breakdown(messages=1000)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows, _ = build_rows(turn)
    names = [r[0] for r in rows]
    assert "Skills" not in names
    assert "Messages" in names


def test_build_rows_sorted_by_tokens_descending():
    bd = make_breakdown(skills=100, memory=500, messages=400)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows, _ = build_rows(turn)
    tokens = [r[1] for r in rows]
    assert tokens == sorted(tokens, reverse=True)


def test_build_rows_for_session():
    session = SessionStats(session_id="abc", display_name="abc1234", first_timestamp=None)
    session.turns.append(make_turn(input_t=500, cache_read=9550, output=50,
                                   breakdown=make_breakdown(skills=300, messages=200)))
    session.turns.append(make_turn(input_t=700, cache_read=10000, output=80,
                                   breakdown=make_breakdown(memory=400, messages=300)))
    rows, totals = build_rows(session)
    categories = {r[0]: r for r in rows}
    assert categories["Skills"][1] == 300
    assert categories["Memory"][1] == 400
    assert totals.input_tokens == 1200
    assert totals.output == 130


def test_build_rows_for_global():
    g = GlobalStats()
    p = ProjectStats(project_slug="p1", display_name="p1")
    s = SessionStats(session_id="s1", display_name="s1abc", first_timestamp=None)
    s.turns.append(make_turn(input_t=100, breakdown=make_breakdown(messages=100)))
    p.sessions = [s]
    g.projects = [p]
    rows, totals = build_rows(g)
    assert totals.input_tokens == 100


def test_build_category_rows_shows_items():
    bd = make_breakdown(skills=500, memory=200, messages=300)
    bd.skills = [
        CategoryItem("BrainstormingSkill", 300),
        CategoryItem("TDDSkill", 200),
    ]
    turn = make_turn(input_t=1000, breakdown=bd)
    rows = build_category_rows(turn, "Skills")
    names = [r[0] for r in rows]
    assert "BrainstormingSkill" in names
    assert "TDDSkill" in names
    tokens = [r[1] for r in rows]
    assert tokens == sorted(tokens, reverse=True)


def test_build_category_rows_empty_category():
    bd = make_breakdown(messages=1000)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows = build_category_rows(turn, "Skills")
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_detail.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `tui/detail.py`**

```python
# tui/detail.py
from __future__ import annotations
from dataclasses import dataclass
from parser.models import (
    CategoryBreakdown, CategoryItem, TurnStats, SessionStats, ProjectStats, GlobalStats, CATEGORIES,
)
from textual.widgets import DataTable
from textual.widget import Widget


@dataclass
class TokenTotals:
    input_tokens: int
    cache_read: int
    cache_create: int
    output: int


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
    elif isinstance(node, CategoryBreakdown):
        cat_totals = node.category_totals()
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


class DetailPane(Widget):
    """Right-pane widget: shows category breakdown + token totals for selected node."""

    DEFAULT_CSS = """
    DetailPane {
        layout: vertical;
    }
    """

    def compose(self):
        yield DataTable(id="category-table")
        yield DataTable(id="totals-table")

    def on_mount(self) -> None:
        cat_table = self.query_one("#category-table", DataTable)
        cat_table.add_columns("Category", "Tokens", "%")

        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.add_columns("", "Tokens")

    def update(self, node) -> None:
        """Refresh both tables for the given stats node."""
        rows, totals = build_rows(node)

        cat_table = self.query_one("#category-table", DataTable)
        cat_table.clear()
        for name, tokens, pct in rows:
            cat_table.add_row(name, f"{tokens:,}", f"{pct:.1f}%")

        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.clear()
        totals_table.add_row("Input (fresh)", f"{totals.input_tokens:,}")
        totals_table.add_row("Cache read", f"{totals.cache_read:,}")
        totals_table.add_row("Cache write", f"{totals.cache_create:,}")
        totals_table.add_row("Output", f"{totals.output:,}")

    def update_category(self, turn: TurnStats, cat_name: str) -> None:
        """Show individual items within a category for a turn."""
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

        # Totals pane shows the parent turn's totals for context
        _, totals = build_rows(turn)
        totals_table = self.query_one("#totals-table", DataTable)
        totals_table.clear()
        totals_table.add_row("Input (fresh)", f"{totals.input_tokens:,}")
        totals_table.add_row("Cache read", f"{totals.cache_read:,}")
        totals_table.add_row("Cache write", f"{totals.cache_create:,}")
        totals_table.add_row("Output", f"{totals.output:,}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_detail.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tui/detail.py tests/test_detail.py
git commit -m "feat: detail pane with category breakdown rows"
```

---

## Task 6: Tree Widget

**Files:**
- Create: `tui/tree.py`

> **Note on TDD:** Textual widgets require a running app event loop to test properly. Tasks 6 and 7 skip the write-test-first step for TUI code — the import-smoke-test and manual smoke test in Task 8 serve as the verification gate instead. All logic worth unit-testing has been extracted to `parser/` and `tui/detail.py`.

The tree widget is mostly UI wiring — hard to unit test without a running Textual app. Build it as a Textual `Widget` wrapping a `Tree`, and keep the node-data binding logic simple enough to read.

- [ ] **Step 1: Implement `tui/tree.py`**

```python
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
```

- [ ] **Step 2: Verify no import errors**

```bash
python -c "from tui.tree import StatsTree; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tui/tree.py
git commit -m "feat: TUI tree widget with lazy project loading"
```

---

## Task 7: App Layout & Keybindings

**Files:**
- Create: `tui/app.py`

- [ ] **Step 1: Implement `tui/app.py`**

```python
# tui/app.py
from __future__ import annotations
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from parser.loader import list_projects
from parser.models import GlobalStats
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
        # Category-level node: (turn, cat_name) tuple — show items within that category
        if isinstance(data, tuple) and len(data) == 2:
            turn, cat_name = data
            detail.update_category(turn, cat_name)
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
```

- [ ] **Step 2: Verify no import errors**

```bash
python -c "from tui.app import CCAuditApp; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tui/app.py
git commit -m "feat: TUI app with split-pane layout and filter"
```

---

## Task 8: Entry Point & Smoke Test

**Files:**
- Create: `main.py`

- [ ] **Step 1: Create `main.py`**

```python
#!/usr/bin/env python3
# main.py
from tui.app import CCAuditApp

if __name__ == "__main__":
    app = CCAuditApp()
    app.run()
```

- [ ] **Step 2: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass (Tasks 2–5 tests).

- [ ] **Step 3: Smoke test the app with real data**

```bash
python main.py
```

Expected: TUI launches, shows `[ALL PROJECTS]` root node. Expand a project node — sessions appear. Expand a session — turns appear. Select a node — right pane shows breakdown. Press `/` — filter bar appears. Press `Escape` — filter clears. Press `q` — app exits.

- [ ] **Step 4: Final commit**

```bash
git add main.py
git commit -m "feat: entry point — ccaudit TUI complete"
```

---

## Done

The app is runnable with `python main.py`. All parser logic is covered by unit tests. The TUI wires tree selection to the detail pane. Lazy loading keeps startup instant regardless of how many projects exist.
