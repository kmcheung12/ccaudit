from __future__ import annotations
import json as _json
from parser.models import CategoryBreakdown, CategoryItem


def _classify_text_block(text: str, is_last_text: bool) -> str:
    first_line = text.split("\n", 1)[0]
    if first_line.startswith("Base directory") and "/skills/" in first_line:
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
            tool_use_id = block.get("tool_use_id", "")
            tool_name = tool_name_by_id.get(tool_use_id, "")
            cat = "MCP" if tool_name.startswith("mcp__") else "Tools"
            counts[cat] += len(_json.dumps(block.get("content", "")))
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
