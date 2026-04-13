# Block-Level Token Categorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redefine "turn" as one complete human-to-human exchange (including all intermediate tool-call round-trips), then categorize `input_tokens + cache_creation_input_tokens` at the JSON block level using both user and assistant message content.

**Architecture:** The loader groups raw JSONL messages into logical turns by detecting human user messages (content has a human text block) vs intermediate tool-result messages (content is all `tool_result` blocks). Each turn carries its full message chain. The categorizer walks blocks structurally — no regex over flattened text — and scales character proportions against the turn's total fresh token budget.

**Tech Stack:** Python 3.11+, existing `parser/models.py` dataclasses, no new dependencies.

---

## Background: Key Concepts

### Turn Definition

A **turn** starts at a human user message and ends just before the next human user message.

```
Human user message  ← turn N starts
Assistant message   (may contain tool_use blocks)
Tool-result user message  (all tool_result blocks, no human text)
Assistant message   (may contain more tool_use blocks)
Tool-result user message
...
Final assistant message  ← turn N ends here
Human user message  ← turn N+1 starts
```

**Human user message detection:** a user message whose content array contains at least one `text` block that is not injected context (i.e. `_extract_human_text` returns non-empty). Equivalently: not all blocks are `tool_result`.

**Intermediate tool-result user message:** a user message where every block is `type: tool_result`. This is never a turn boundary.

### What contributes to `input_tokens + cache_creation_input_tokens`

For a given turn, the API is called multiple times (once per assistant message in the chain). Each call adds new content to the prompt:

- **First call:** fresh content = human user message blocks
- **Each subsequent call:** fresh content = previous assistant message blocks + tool-result user message blocks

The turn's total fresh token budget is:
```
budget = sum(input_tokens + cache_creation_input_tokens)
         across all assistant messages in the turn
```

### Block Classification Rules

**User message blocks:**

| Block | Category | Detection |
|---|---|---|
| `tool_result` | Tools | Structural — cross-reference `tool_use_id` against preceding assistant's tool_use map for subcategory |
| `text` — first line `Base directory: /.../skills/` | Skills | First-line prefix check |
| `text` — first line `---`, contains `name:` | Memory | First-line prefix check |
| `text` — contains `<system-reminder>` | Tools | Tag presence check |
| `text` — last non-injected block | Messages | By elimination (human input) |
| `text` — anything else | Other | |

**Assistant message blocks:**

| Block | Category | Detection |
|---|---|---|
| `text` | Messages | Structural |
| `tool_use` — `name == "Agent"` | Agents | Exact match |
| `tool_use` — `name` starts with `mcp__` | **MCP** | Prefix check |
| `tool_use` — all other names | Tools | Catch-all |

**Character counting:**
- Text block: `len(block["text"])`
- `tool_result` block: `len(json.dumps(block.get("content", "")))`
- `tool_use` block: `len(block["name"]) + len(json.dumps(block.get("input", {})))`

---

## File Map

| File | Change |
|---|---|
| `parser/models.py` | Add `"MCP"` to `CATEGORIES`; add `mcp_tools: list[CategoryItem]` to `CategoryBreakdown` |
| `parser/loader.py` | Replace sequential pairing with turn-group detection; pass block content to new categorizer |
| `parser/categorizer.py` | Replace regex-over-text with block-level classification; emit MCP separately |
| `tui/detail.py` | Add MCP colour to `_CAT_STYLE` |
| `tests/test_loader.py` | New — turn grouping tests |
| `tests/test_categorizer.py` | New — block classification tests |

---

## Task 0: Add MCP as a separate category in models and display

**Files:**
- Modify: `parser/models.py`
- Modify: `tui/detail.py`

MCP tool calls (names starting `mcp__`) are distinct from built-in Claude Code tools (`Read`, `Bash`, `Grep`, etc.) and should surface as their own category so users can see how much context MCP servers consume.

- [ ] **Step 1: Add `"MCP"` to `CATEGORIES` and `CategoryBreakdown` in models.py**

```python
# parser/models.py

CATEGORIES = ["Skills", "Memory", "Tools", "MCP", "Agents", "Messages", "Other"]

@dataclass
class CategoryBreakdown:
    skills: list[CategoryItem] = field(default_factory=list)
    memory: list[CategoryItem] = field(default_factory=list)
    tools: list[CategoryItem] = field(default_factory=list)
    mcp_tools: list[CategoryItem] = field(default_factory=list)
    agents: list[CategoryItem] = field(default_factory=list)
    messages_tokens: int = 0
    other_tokens: int = 0

    def category_totals(self) -> dict[str, int]:
        return {
            "Skills":   sum(i.tokens for i in self.skills),
            "Memory":   sum(i.tokens for i in self.memory),
            "Tools":    sum(i.tokens for i in self.tools),
            "MCP":      sum(i.tokens for i in self.mcp_tools),
            "Agents":   sum(i.tokens for i in self.agents),
            "Messages": self.messages_tokens,
            "Other":    self.other_tokens,
        }

    def total_attributed_tokens(self) -> int:
        return sum(self.category_totals().values())
```

Update `_merge_breakdowns` to include `mcp_tools`:

```python
def _merge_breakdowns(breakdowns: list[CategoryBreakdown]) -> CategoryBreakdown:
    merged = CategoryBreakdown()
    for bd in breakdowns:
        merged.skills.extend(bd.skills)
        merged.memory.extend(bd.memory)
        merged.tools.extend(bd.tools)
        merged.mcp_tools.extend(bd.mcp_tools)
        merged.agents.extend(bd.agents)
        merged.messages_tokens += bd.messages_tokens
        merged.other_tokens += bd.other_tokens
    return merged
```

- [ ] **Step 2: Add MCP colour to `_CAT_STYLE` in `tui/detail.py`**

```python
_CAT_STYLE = {
    "Messages": "bright_magenta",
    "Skills":   "bright_yellow",
    "Memory":   "bright_green",
    "Tools":    "bright_blue",
    "MCP":      "bright_red",
    "Agents":   "bright_cyan",
    "Other":    "white",
}
```

- [ ] **Step 3: Smoke-test the TUI still launches**

```bash
cd /Users/alan/code/ccaudit
python -m ccaudit  # or however the app is launched; confirm no import errors
```

Expected: app opens, category table headers show MCP column.

- [ ] **Step 4: Commit**

```bash
git add parser/models.py tui/detail.py
git commit -m "feat: add MCP as a separate token category"
```

---

## Task 1: Detect human vs intermediate user messages

**Files:**
- Modify: `parser/loader.py`
- Create: `tests/test_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_loader.py
from parser.loader import _is_human_user_message

def test_human_message_with_text_block():
    content = [
        {"type": "text", "text": "Base directory: /skills/foo\n# Foo"},
        {"type": "text", "text": "hello world"},
    ]
    assert _is_human_user_message(content) is True

def test_tool_result_only_is_not_human():
    content = [
        {"type": "tool_result", "tool_use_id": "toolu_01", "content": "ok"},
        {"type": "tool_result", "tool_use_id": "toolu_02", "content": "ok"},
    ]
    assert _is_human_user_message(content) is False

def test_string_content_is_human():
    assert _is_human_user_message("hello") is True

def test_empty_content_is_not_human():
    assert _is_human_user_message([]) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/alan/code/ccaudit
python -m pytest tests/test_loader.py::test_human_message_with_text_block -v
```

Expected: `ImportError` or `FAIL` — `_is_human_user_message` does not exist yet.

- [ ] **Step 3: Implement `_is_human_user_message` in loader.py**

Add after the existing `_extract_human_text` function:

```python
def _is_human_user_message(content) -> bool:
    """Return True if this user message contains a human-typed turn (not purely tool results)."""
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list) or not content:
        return False
    return any(
        block.get("type") != "tool_result"
        for block in content
        if isinstance(block, dict)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_loader.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parser/loader.py tests/test_loader.py
git commit -m "feat: add _is_human_user_message for turn boundary detection"
```

---

## Task 2: Group raw JSONL messages into turn chains

**Files:**
- Modify: `parser/loader.py`
- Modify: `tests/test_loader.py`

A **turn chain** is: one human user message + zero or more (assistant, tool-result-user) pairs + one final assistant message.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_loader.py — add to existing file
from parser.loader import _group_turns

def _msg(type_, role=None, content=None, usage=None):
    m = {"type": type_}
    if role:
        m["message"] = {"role": role, "content": content or [], "usage": usage}
    return m

def test_single_turn_no_tools():
    msgs = [
        _msg("user",      "user",      [{"type": "text", "text": "hi"}]),
        _msg("assistant", "assistant", [{"type": "text", "text": "hello"}],
             usage={"input_tokens": 10, "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0, "output_tokens": 5}),
    ]
    turns = _group_turns(msgs)
    assert len(turns) == 1
    assert len(turns[0]["assistant_msgs"]) == 1
    assert len(turns[0]["intermediate_pairs"]) == 0

def test_turn_with_one_tool_roundtrip():
    msgs = [
        _msg("user",      "user",      [{"type": "text", "text": "hi"}]),
        _msg("assistant", "assistant", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
             usage={"input_tokens": 10, "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 0, "output_tokens": 3}),
        _msg("user",      "user",      [{"type": "tool_result", "tool_use_id": "t1", "content": "data"}]),
        _msg("assistant", "assistant", [{"type": "text", "text": "done"}],
             usage={"input_tokens": 1, "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 50, "output_tokens": 4}),
    ]
    turns = _group_turns(msgs)
    assert len(turns) == 1
    assert len(turns[0]["intermediate_pairs"]) == 1
    assert len(turns[0]["assistant_msgs"]) == 2

def test_two_human_turns():
    usage = {"input_tokens": 5, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 0, "output_tokens": 2}
    msgs = [
        _msg("user",      "user",      [{"type": "text", "text": "first"}]),
        _msg("assistant", "assistant", [{"type": "text", "text": "reply"}], usage=usage),
        _msg("user",      "user",      [{"type": "text", "text": "second"}]),
        _msg("assistant", "assistant", [{"type": "text", "text": "reply2"}], usage=usage),
    ]
    turns = _group_turns(msgs)
    assert len(turns) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_loader.py::test_single_turn_no_tools -v
```

Expected: `ImportError` — `_group_turns` does not exist yet.

- [ ] **Step 3: Implement `_group_turns` in loader.py**

Add after `_is_human_user_message`:

```python
def _group_turns(raw_messages: list[dict]) -> list[dict]:
    """
    Group raw JSONL messages into logical turns.

    Each turn dict:
        human_user_msg:     the opening user message dict
        intermediate_pairs: list of (assistant_msg, tool_result_user_msg) dicts
        final_assistant_msg: the closing assistant message dict (has usage)
        after_compact:      bool

    Assistant messages without usage are skipped (streaming artifacts).
    System compact_boundary messages reset the after_compact flag.
    """
    turns = []
    after_compact = False
    pending_human: dict | None = None
    pending_intermediates: list[tuple[dict, dict]] = []
    pending_assistant: dict | None = None  # last assistant seen, awaiting tool-result pair

    for msg in raw_messages:
        msg_type = msg.get("type")

        if msg_type == "system" and msg.get("subtype") == "compact_boundary":
            after_compact = True
            continue

        if msg_type == "user":
            content = msg.get("message", {}).get("content", [])
            if _is_human_user_message(content):
                # Close any open turn first (shouldn't happen in well-formed JSONL,
                # but guard against it)
                if pending_human is not None and pending_assistant is not None:
                    turns.append({
                        "human_user_msg": pending_human,
                        "intermediate_pairs": pending_intermediates,
                        "final_assistant_msg": pending_assistant,
                        "after_compact": after_compact,
                    })
                pending_human = msg
                pending_intermediates = []
                pending_assistant = None
                after_compact = after_compact  # carry through
            else:
                # Tool-result message: pair with the last assistant
                if pending_assistant is not None:
                    pending_intermediates.append((pending_assistant, msg))
                    pending_assistant = None

        elif msg_type == "assistant":
            usage = msg.get("message", {}).get("usage")
            if not usage:
                continue  # skip streaming artifacts
            if pending_human is None:
                continue  # assistant before any human message, skip
            pending_assistant = msg

        # After closing a turn the after_compact flag resets
    # Flush the last open turn
    if pending_human is not None and pending_assistant is not None:
        turns.append({
            "human_user_msg": pending_human,
            "intermediate_pairs": pending_intermediates,
            "final_assistant_msg": pending_assistant,
            "after_compact": after_compact,
        })

    return turns
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_loader.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parser/loader.py tests/test_loader.py
git commit -m "feat: implement _group_turns for human-to-human turn grouping"
```

---

## Task 3: Block-level classifier for user message content

**Files:**
- Modify: `parser/categorizer.py`
- Create: `tests/test_categorizer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_categorizer.py
import json
from parser.categorizer import classify_user_blocks

def test_tool_result_block_is_tools():
    content = [{"type": "tool_result", "tool_use_id": "t1", "content": "file data"}]
    result = classify_user_blocks(content, tool_name_by_id={"t1": "Read"})
    assert result["Tools"] > 0
    assert result["Messages"] == 0

def test_skills_text_block():
    text = "Base directory: /home/user/.claude/skills/foo\n# Foo Skill\nsome content here"
    content = [{"type": "text", "text": text}]
    result = classify_user_blocks(content, tool_name_by_id={})
    assert result["Skills"] > 0
    assert result["Messages"] == 0

def test_memory_text_block():
    text = "---\nname: my-memory\ndescription: test\ntype: user\n---\nsome content"
    content = [{"type": "text", "text": text}]
    result = classify_user_blocks(content, tool_name_by_id={})
    assert result["Memory"] > 0

def test_system_reminder_text_block():
    text = "<system-reminder>hook output here</system-reminder>"
    content = [{"type": "text", "text": text}]
    result = classify_user_blocks(content, tool_name_by_id={})
    assert result["Tools"] > 0

def test_human_text_is_messages():
    content = [{"type": "text", "text": "please fix this bug"}]
    result = classify_user_blocks(content, tool_name_by_id={})
    assert result["Messages"] > 0

def test_mixed_content_splits_correctly():
    content = [
        {"type": "text", "text": "Base directory: /skills/foo\n# Foo\ncontent"},
        {"type": "tool_result", "tool_use_id": "t1", "content": "read result"},
        {"type": "text", "text": "please help"},
    ]
    result = classify_user_blocks(content, tool_name_by_id={"t1": "Read"})
    assert result["Skills"] > 0
    assert result["Tools"] > 0
    assert result["Messages"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_categorizer.py -v
```

Expected: `ImportError` — `classify_user_blocks` does not exist yet.

- [ ] **Step 3: Implement `classify_user_blocks` in categorizer.py**

Add to `categorizer.py` (keep existing functions for now):

```python
import json as _json

def _classify_text_block(text: str, is_last_text: bool) -> str:
    """Classify a text block into a category name."""
    first_line = text.split("\n", 1)[0]
    if first_line.startswith("Base directory:") and "/skills/" in first_line:
        return "Skills"
    if first_line.strip() == "---" and "name:" in text:
        return "Memory"
    if "<system-reminder>" in text:
        return "Tools"
    # Last non-injected text block is the human message
    if is_last_text:
        return "Messages"
    return "Other"


def classify_user_blocks(
    content,
    tool_name_by_id: dict[str, str],
) -> dict[str, int]:
    """
    Classify blocks in a user message content array.
    Returns dict of category → total character count.
    """
    counts: dict[str, int] = {
        "Skills": 0, "Memory": 0, "Tools": 0,
        "Agents": 0, "Messages": 0, "Other": 0,
    }

    if isinstance(content, str):
        counts["Messages"] += len(content)
        return counts

    if not isinstance(content, list):
        return counts

    # Find index of last text block (the human message candidate)
    last_text_idx = -1
    for i, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "text":
            last_text_idx = i

    for i, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        btype = block.get("type")

        if btype == "tool_result":
            inner = block.get("content", "")
            char_count = len(_json.dumps(inner))
            counts["Tools"] += char_count

        elif btype == "text":
            text = block.get("text", "")
            is_last = (i == last_text_idx)
            cat = _classify_text_block(text, is_last)
            counts[cat] += len(text)

    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_categorizer.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parser/categorizer.py tests/test_categorizer.py
git commit -m "feat: implement classify_user_blocks for block-level user message categorization"
```

---

## Task 4: Block-level classifier for assistant message content

**Files:**
- Modify: `parser/categorizer.py`
- Modify: `tests/test_categorizer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_categorizer.py — add to existing file
from parser.categorizer import classify_assistant_blocks

def test_text_block_is_messages():
    content = [{"type": "text", "text": "Here is the result."}]
    result = classify_assistant_blocks(content)
    assert result["Messages"] > 0

def test_agent_tool_use():
    content = [{"type": "tool_use", "id": "t1", "name": "Agent",
                "input": {"prompt": "do something", "subagent_type": "general-purpose"}}]
    result = classify_assistant_blocks(content)
    assert result["Agents"] > 0
    assert result["Tools"] == 0

def test_mcp_tool_use():
    content = [{"type": "tool_use", "id": "t1", "name": "mcp__slack__read_channel",
                "input": {"channel": "general"}}]
    result = classify_assistant_blocks(content)
    assert result["MCP"] > 0
    assert result["Tools"] == 0

def test_builtin_tool_use():
    content = [{"type": "tool_use", "id": "t1", "name": "Read",
                "input": {"file_path": "/foo/bar.py"}}]
    result = classify_assistant_blocks(content)
    assert result["Tools"] > 0

def test_mixed_assistant_content():
    content = [
        {"type": "text", "text": "I will read the file."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/foo.py"}},
    ]
    result = classify_assistant_blocks(content)
    assert result["Messages"] > 0
    assert result["Tools"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_categorizer.py::test_text_block_is_messages -v
```

Expected: `ImportError` — `classify_assistant_blocks` does not exist yet.

- [ ] **Step 3: Implement `classify_assistant_blocks` in categorizer.py**

Add to `categorizer.py`:

```python
def classify_assistant_blocks(content) -> dict[str, int]:
    """
    Classify blocks in an assistant message content array.
    Returns dict of category → total character count.
    """
    counts: dict[str, int] = {
        "Skills": 0, "Memory": 0, "Tools": 0,
        "MCP": 0, "Agents": 0, "Messages": 0, "Other": 0,
    }

    if isinstance(content, str):
        counts["Messages"] += len(content)
        return counts

    if not isinstance(content, list):
        return counts

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")

        if btype == "text":
            counts["Messages"] += len(block.get("text", ""))

        elif btype == "tool_use":
            name = block.get("name", "")
            char_count = len(name) + len(_json.dumps(block.get("input", {})))
            if name == "Agent":
                counts["Agents"] += char_count
            elif name.startswith("mcp__"):
                counts["MCP"] += char_count
            else:
                counts["Tools"] += char_count

    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_categorizer.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parser/categorizer.py tests/test_categorizer.py
git commit -m "feat: implement classify_assistant_blocks for block-level assistant categorization"
```

---

## Task 5: Combine blocks into a CategoryBreakdown

**Files:**
- Modify: `parser/categorizer.py`
- Modify: `tests/test_categorizer.py`

This replaces the existing `categorize(text, input_tokens)` function.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_categorizer.py — add to existing file
from parser.categorizer import categorize_turn
from parser.models import CategoryBreakdown

def test_categorize_turn_attributes_tool_result_to_tools():
    # Turn with: human message + tool result from prior turn + assistant text response
    human_content = [
        {"type": "tool_result", "tool_use_id": "t1", "content": "file content " * 50},
        {"type": "text", "text": "thanks"},
    ]
    # The assistant that made the tool call (intermediate in this turn)
    prior_assistant_content = [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/foo.py"}},
    ]
    bd = categorize_turn(
        human_content=human_content,
        intermediate_pairs=[],  # no further tool round-trips this turn
        prior_assistant_content=prior_assistant_content,
        fresh_tokens=1000,
    )
    assert isinstance(bd, CategoryBreakdown)
    tools_total = sum(i.tokens for i in bd.tools)
    assert tools_total > 0
    assert bd.messages_tokens > 0

def test_categorize_turn_budget_sums_to_fresh_tokens():
    human_content = [{"type": "text", "text": "hello world"}]
    bd = categorize_turn(
        human_content=human_content,
        intermediate_pairs=[],
        prior_assistant_content=[],
        fresh_tokens=500,
    )
    total = sum(bd.category_totals().values())
    assert total == 500
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_categorizer.py::test_categorize_turn_attributes_tool_result_to_tools -v
```

Expected: `ImportError` — `categorize_turn` does not exist.

- [ ] **Step 3: Implement `categorize_turn` in categorizer.py**

Add to `categorizer.py`:

```python
from parser.models import CategoryBreakdown, CategoryItem

def categorize_turn(
    human_content,
    intermediate_pairs: list[tuple],
    prior_assistant_content,
    fresh_tokens: int,
) -> CategoryBreakdown:
    """
    Build a CategoryBreakdown for one logical turn.

    human_content         — content array of the opening human user message
    intermediate_pairs    — list of (assistant_content, tool_result_user_content)
                            for each tool-call round-trip within this turn
    prior_assistant_content — content array of the assistant message immediately
                              preceding this turn's human message (its tool_use
                              blocks generated the tool_results in human_content)
    fresh_tokens          — sum of (input_tokens + cache_creation_input_tokens)
                            across all assistant messages in this turn
    """
    # Build tool_use_id → tool_name map from prior assistant
    tool_name_by_id: dict[str, str] = {}
    if isinstance(prior_assistant_content, list):
        for block in prior_assistant_content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_name_by_id[block["id"]] = block.get("name", "")

    # Accumulate char counts per category across all blocks in the turn
    totals: dict[str, int] = {
        "Skills": 0, "Memory": 0, "Tools": 0,
        "MCP": 0, "Agents": 0, "Messages": 0, "Other": 0,
    }

    def _add(counts: dict[str, int]) -> None:
        for cat, n in counts.items():
            totals[cat] = totals.get(cat, 0) + n

    # Human user message
    _add(classify_user_blocks(human_content, tool_name_by_id))

    # Each intermediate (assistant → tool_result_user) pair
    for asst_content, tr_content in intermediate_pairs:
        _add(classify_assistant_blocks(asst_content))
        # tool_name_by_id for this pair comes from asst_content itself
        pair_tool_map = {
            b["id"]: b.get("name", "")
            for b in (asst_content if isinstance(asst_content, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        _add(classify_user_blocks(tr_content, pair_tool_map))

    total_chars = sum(totals.values()) or 1

    def scale(char_count: int) -> int:
        return round((char_count / total_chars) * fresh_tokens)

    bd = CategoryBreakdown()
    bd.skills     = [CategoryItem(name="Skills",  tokens=scale(totals["Skills"]))]  if totals["Skills"]  else []
    bd.memory     = [CategoryItem(name="Memory",  tokens=scale(totals["Memory"]))]  if totals["Memory"]  else []
    bd.tools      = [CategoryItem(name="Tools",   tokens=scale(totals["Tools"]))]   if totals["Tools"]   else []
    bd.mcp_tools  = [CategoryItem(name="MCP",     tokens=scale(totals["MCP"]))]     if totals["MCP"]     else []
    bd.agents     = [CategoryItem(name="Agents",  tokens=scale(totals["Agents"]))]  if totals["Agents"]  else []
    bd.messages_tokens = scale(totals["Messages"])

    # Absorb rounding drift into Other
    attributed = (
        sum(i.tokens for i in bd.skills)
        + sum(i.tokens for i in bd.memory)
        + sum(i.tokens for i in bd.tools)
        + sum(i.tokens for i in bd.agents)
        + bd.messages_tokens
    )
    bd.other_tokens = max(0, fresh_tokens - attributed)
    return bd
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_categorizer.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parser/categorizer.py tests/test_categorizer.py
git commit -m "feat: implement categorize_turn combining block classifiers"
```

---

## Task 6: Wire up new turn grouping and categorizer in load_session

**Files:**
- Modify: `parser/loader.py`
- Modify: `tests/test_loader.py`

Replace the existing sequential pairing loop in `load_session` with `_group_turns` + `categorize_turn`.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_loader.py — add to existing file
import json, tempfile
from pathlib import Path
from parser.loader import load_session
from parser.models import SessionStats

def _write_jsonl(msgs: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for m in msgs:
        f.write(json.dumps(m) + "\n")
    f.close()
    return Path(f.name)

def _user_msg(content, timestamp="2026-01-01T00:00:00Z"):
    return {"type": "user", "timestamp": timestamp,
            "message": {"role": "user", "content": content}}

def _asst_msg(content, inp=10, cc=5, cr=0, out=3, timestamp="2026-01-01T00:01:00Z"):
    return {"type": "assistant", "timestamp": timestamp,
            "message": {"role": "assistant", "content": content,
                        "usage": {"input_tokens": inp,
                                  "cache_creation_input_tokens": cc,
                                  "cache_read_input_tokens": cr,
                                  "output_tokens": out}}}

def test_load_session_single_turn():
    msgs = [
        _user_msg([{"type": "text", "text": "hello"}]),
        _asst_msg([{"type": "text", "text": "hi"}]),
    ]
    path = _write_jsonl(msgs)
    session = load_session(path)
    assert len(session.turns) == 1
    assert session.turns[0].turn_number == 1
    assert session.turns[0].input_tokens == 10
    assert session.turns[0].cache_create_tokens == 5

def test_load_session_tool_roundtrip_is_one_turn():
    msgs = [
        _user_msg([{"type": "text", "text": "read foo.py"}]),
        _asst_msg([{"type": "tool_use", "id": "t1", "name": "Read",
                    "input": {"file_path": "/foo.py"}}], inp=10, cc=5, out=2),
        _user_msg([{"type": "tool_result", "tool_use_id": "t1", "content": "x = 1"}]),
        _asst_msg([{"type": "text", "text": "done"}], inp=1, cc=20, cr=50, out=4),
    ]
    path = _write_jsonl(msgs)
    session = load_session(path)
    assert len(session.turns) == 1
    # fresh budget = both assistant messages' (input + cache_create)
    assert session.turns[0].input_tokens == 11   # 10 + 1
    assert session.turns[0].cache_create_tokens == 25  # 5 + 20
    # Tools should be non-zero (tool_result block + tool_use block)
    cat = session.turns[0].category_breakdown.category_totals()
    assert cat["Tools"] > 0

def test_load_session_two_human_turns():
    usage = dict(inp=5, cc=2, cr=0, out=1)
    msgs = [
        _user_msg([{"type": "text", "text": "first"}]),
        _asst_msg([{"type": "text", "text": "reply"}], **usage),
        _user_msg([{"type": "text", "text": "second"}]),
        _asst_msg([{"type": "text", "text": "reply2"}], **usage),
    ]
    path = _write_jsonl(msgs)
    session = load_session(path)
    assert len(session.turns) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_loader.py::test_load_session_tool_roundtrip_is_one_turn -v
```

Expected: FAIL — tool roundtrip is currently counted as two turns.

- [ ] **Step 3: Replace the turn-building loop in `load_session`**

In `parser/loader.py`, replace the section starting at `# Build turns: pair each user message with the next assistant message` through the end of the turns loop with:

```python
    # Group messages into logical turns (human → ... → final assistant)
    turn_groups = _group_turns(raw_messages)

    turns: list[TurnStats] = []
    prev_assistant_content = []  # assistant content from the preceding turn

    for turn_number, group in enumerate(turn_groups, start=1):
        human_msg = group["human_user_msg"]
        intermediate_pairs = group["intermediate_pairs"]
        final_asst = group["final_assistant_msg"]

        human_content = human_msg.get("message", {}).get("content", [])
        prior_asst_content = prev_assistant_content

        # Sum token usage across all assistant messages in this turn
        all_asst_msgs = (
            [asst for asst, _ in intermediate_pairs] + [final_asst]
        )
        input_tokens = sum(
            m.get("message", {}).get("usage", {}).get("input_tokens", 0)
            for m in all_asst_msgs
        )
        cache_create = sum(
            m.get("message", {}).get("usage", {}).get("cache_creation_input_tokens", 0)
            for m in all_asst_msgs
        )
        cache_read = sum(
            m.get("message", {}).get("usage", {}).get("cache_read_input_tokens", 0)
            for m in all_asst_msgs
        )
        output_tokens = sum(
            m.get("message", {}).get("usage", {}).get("output_tokens", 0)
            for m in all_asst_msgs
        )

        # Intermediate pairs: (asst_content, tool_result_user_content)
        intermediate_block_pairs = [
            (
                asst.get("message", {}).get("content", []),
                tr_user.get("message", {}).get("content", []),
            )
            for asst, tr_user in intermediate_pairs
        ]

        fresh_tokens = input_tokens + cache_create
        breakdown = categorize_turn(
            human_content=human_content,
            intermediate_pairs=intermediate_block_pairs,
            prior_assistant_content=prior_asst_content,
            fresh_tokens=fresh_tokens,
        )

        final_asst_content = final_asst.get("message", {}).get("content", [])
        assistant_text = _extract_assistant_text(final_asst_content)
        files_read, tool_calls = _extract_tool_calls(final_asst_content)

        turns.append(TurnStats(
            turn_number=turn_number,
            timestamp=final_asst.get("timestamp", ""),
            input_tokens=input_tokens,
            cache_read_tokens=cache_read,
            cache_create_tokens=cache_create,
            output_tokens=output_tokens,
            category_breakdown=breakdown,
            after_compact=group["after_compact"],
            user_text=_extract_human_text(human_content),
            assistant_text=assistant_text,
            files_read=files_read,
            tool_calls=tool_calls,
            raw_user=human_msg,
            raw_assistant=final_asst,
            jsonl_path=str(jsonl_file),
        ))

        prev_assistant_content = final_asst_content
```

Also add the import at the top of `load_session`:

```python
from parser.categorizer import categorize_turn
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Smoke-test against real data**

```bash
python -c "
from parser.loader import load_session
from pathlib import Path
import glob

files = sorted(glob.glob(str(Path.home() / '.claude/projects/*/*.jsonl')))[:3]
for f in files:
    s = load_session(Path(f))
    print(f'{Path(f).name[:8]}: {len(s.turns)} turns')
    for t in s.turns[:2]:
        cats = {k: v for k, v in t.category_breakdown.category_totals().items() if v > 0}
        print(f'  T{t.turn_number}: fresh={t.input_tokens+t.cache_create_tokens:,}  {cats}')
"
```

Expected: turns load, categories non-zero, no exceptions.

- [ ] **Step 6: Commit**

```bash
git add parser/loader.py tests/test_loader.py
git commit -m "feat: replace sequential turn pairing with human-boundary turn grouping and block-level categorization"
```

---

## Task 7: Remove dead code

**Files:**
- Modify: `parser/categorizer.py`

The old `categorize(text, input_tokens)`, `extract_categories(text)`, and all regex patterns are no longer called. Remove them to keep the module clean.

- [ ] **Step 1: Delete the old functions and patterns from categorizer.py**

Remove:
- `_SKILL_PATTERN`
- `_MEMORY_PATTERN`
- `_SYSTEM_REMINDER_PATTERN`
- `_FUNCTION_RESULTS_PATTERN`
- `_AGENT_PATTERN`
- `extract_categories(text)`
- `categorize(text, input_tokens)`

Keep:
- `_json` import
- `classify_user_blocks`
- `classify_assistant_blocks`
- `_classify_text_block`
- `categorize_turn`

- [ ] **Step 2: Run all tests to confirm nothing broke**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add parser/categorizer.py
git commit -m "chore: remove legacy regex-based categorizer now replaced by block-level approach"
```

---

## Self-Review

**Spec coverage:**
- ✅ Turn = human message to next human message, tool round-trips collapsed into one turn
- ✅ Block-level classification — no regex over flattened text
- ✅ `tool_result` blocks → Tools (structural, not regex)
- ✅ `tool_use` blocks in assistant messages → Tools or Agents by name
- ✅ Token budget = sum across all assistant messages in the turn
- ✅ `prior_assistant_content` passed for cross-referencing tool_use_id → tool_name
- ✅ `cache_read_input_tokens` remains the `░` bar, unchanged
- ✅ Rounding drift → Other

**Notes:**
- MCP tool subcategory (mcp__*) is classified into Tools generically; a future plan could further split into "Tools (MCP)" vs "Tools (built-in)" by storing the tool name in `CategoryItem.name`
- `parentUuid` links each message to its immediate predecessor and can restore order in out-of-order JSONL, but does not encode turn boundaries — human vs intermediate detection remains content-based
- The `after_compact` flag on the turn group is set when the human message followed a compact boundary; the token accounting (summing across all assistant messages) is still correct post-compaction
