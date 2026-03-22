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
