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
        # Find the actual end of this skill block (before next Base directory:)
        next_base = text.find("\nBase directory:", m.end())
        block_end = next_base if next_base != -1 else len(text)
        skill_name = m.group(1).strip()
        consume(block_start, block_end, "Skills", skill_name)

    # Memory: YAML frontmatter --- name: ... ---
    for m in _MEMORY_PATTERN.finditer(text):
        if any(consumed[m.start():m.end()]):
            continue
        name_match = re.search(r"name:\s*(\S+)", m.group(0))
        name = name_match.group(1) if name_match else "memory"
        consume(m.start(), m.end(), "Memory", name)

    # Tools: <function_results> and <system-reminder>
    # Process <function_results> first so that system-reminder tags embedded inside
    # a tool result don't get consumed before the outer block is matched.
    for pattern in (_FUNCTION_RESULTS_PATTERN, _SYSTEM_REMINDER_PATTERN):
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


def categorize(text: str, input_tokens: int) -> CategoryBreakdown:
    """
    Build a CategoryBreakdown for one turn.

    input_tokens -- full fresh budget: input_tokens + cache_creation_input_tokens.
                    Both are freshly computed this turn and their content is present
                    in the user message text, so text proportion gives correct attribution.
    cache_read_input_tokens are not passed; they are shown as ░ in the bar chart.
    Other receives any rounding drift or tokens that no pattern could attribute.
    """
    bd = CategoryBreakdown()

    if not text.strip():
        bd.other_tokens = input_tokens
        return bd

    cats = extract_categories(text)
    total_chars = sum(
        item.tokens  # here tokens = char_count placeholder
        for items in cats.values()
        for item in items
    )

    if total_chars == 0:
        bd.other_tokens = input_tokens
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

    # Other: rounding drift and any tokens no pattern could attribute.
    attributed = (
        sum(i.tokens for i in bd.skills)
        + sum(i.tokens for i in bd.memory)
        + sum(i.tokens for i in bd.tools)
        + sum(i.tokens for i in bd.agents)
        + bd.messages_tokens
    )
    bd.other_tokens = max(0, input_tokens - attributed)

    return bd


import json as _json


def _classify_text_block(text: str, is_last_text: bool) -> str:
    first_line = text.split("\n", 1)[0]
    if first_line.startswith("Base directory:") and "/skills/" in first_line:
        return "Skills"
    if first_line.strip() == "---" and "name:" in text:
        return "Memory"
    if "<system-reminder>" in text:
        return "Tools"
    if is_last_text:
        return "Messages"
    return "Other"


def classify_user_blocks(content, tool_name_by_id: dict) -> dict:
    counts = {"Skills": 0, "Memory": 0, "Tools": 0, "MCP": 0, "Agents": 0, "Messages": 0, "Other": 0}
    if isinstance(content, str):
        counts["Messages"] += len(content)
        return counts
    if not isinstance(content, list):
        return counts
    last_text_idx = -1
    for i, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "text":
            last_text_idx = i
    for i, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_result":
            counts["Tools"] += len(_json.dumps(block.get("content", "")))
        elif btype == "text":
            text = block.get("text", "")
            cat = _classify_text_block(text, i == last_text_idx)
            counts[cat] += len(text)
    return counts


def classify_assistant_blocks(content) -> dict:
    counts = {"Skills": 0, "Memory": 0, "Tools": 0, "MCP": 0, "Agents": 0, "Messages": 0, "Other": 0}
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


def categorize_turn(human_content, intermediate_pairs, prior_assistant_content, fresh_tokens: int) -> CategoryBreakdown:
    tool_name_by_id: dict = {}
    if isinstance(prior_assistant_content, list):
        for block in prior_assistant_content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_name_by_id[block["id"]] = block.get("name", "")

    totals = {"Skills": 0, "Memory": 0, "Tools": 0, "MCP": 0, "Agents": 0, "Messages": 0, "Other": 0}

    def _add(counts: dict) -> None:
        for cat, n in counts.items():
            totals[cat] = totals.get(cat, 0) + n

    _add(classify_user_blocks(human_content, tool_name_by_id))

    for asst_content, tr_content in intermediate_pairs:
        _add(classify_assistant_blocks(asst_content))
        pair_tool_map = {
            b["id"]: b.get("name", "")
            for b in (asst_content if isinstance(asst_content, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        _add(classify_user_blocks(tr_content, pair_tool_map))

    total_chars = sum(totals.values()) or 1

    def scale(n: int) -> int:
        return round((n / total_chars) * fresh_tokens)

    bd = CategoryBreakdown()
    bd.skills    = [CategoryItem(name="Skills",  tokens=scale(totals["Skills"]))]  if totals["Skills"]  else []
    bd.memory    = [CategoryItem(name="Memory",  tokens=scale(totals["Memory"]))]  if totals["Memory"]  else []
    bd.tools     = [CategoryItem(name="Tools",   tokens=scale(totals["Tools"]))]   if totals["Tools"]   else []
    bd.mcp_tools = [CategoryItem(name="MCP",     tokens=scale(totals["MCP"]))]     if totals["MCP"]     else []
    bd.agents    = [CategoryItem(name="Agents",  tokens=scale(totals["Agents"]))]  if totals["Agents"]  else []
    bd.messages_tokens = scale(totals["Messages"])

    attributed = (
        sum(i.tokens for i in bd.skills)
        + sum(i.tokens for i in bd.memory)
        + sum(i.tokens for i in bd.tools)
        + sum(i.tokens for i in bd.mcp_tools)
        + sum(i.tokens for i in bd.agents)
        + bd.messages_tokens
    )
    bd.other_tokens = max(0, fresh_tokens - attributed)
    return bd
