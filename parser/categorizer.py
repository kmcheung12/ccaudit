from __future__ import annotations
import json as _json
from parser.models import CategoryBreakdown, CategoryItem


CHARS_PER_TOKEN = 4  # rough English/code heuristic; errs toward attributing more to "Other"

# Codex tool names that spawn a sub-agent rather than doing local work.
CODEX_AGENT_TOOLS = {"agent", "run_agent", "spawn_agent", "start_agent", "delegate", "subagent"}


def _empty_counts() -> dict:
    return {"Skills": 0, "Tools": 0, "MCP": 0, "Agents": 0, "Messages": 0, "Reasoning": 0, "Other": 0}


def _build_breakdown(totals: dict, fresh_tokens: int) -> CategoryBreakdown:
    """Scale raw per-category char counts into a token budget of `fresh_tokens`.

    The visible content is estimated from character counts; anything the model
    was billed for beyond that (system prompt, tool schemas, ...) is invisible
    overhead and lands in "Other", along with any rounding remainder.
    """
    total_chars = sum(totals.values()) or 1

    visible_token_estimate = max(1, total_chars // CHARS_PER_TOKEN)
    invisible_overhead = max(0, fresh_tokens - visible_token_estimate)

    def scale(n: int) -> int:
        return round((n / total_chars) * visible_token_estimate)

    bd = CategoryBreakdown()
    bd.skills    = [CategoryItem(name="Skills",  tokens=scale(totals["Skills"]))]  if totals["Skills"]  else []
    bd.tools     = [CategoryItem(name="Tools",   tokens=scale(totals["Tools"]))]   if totals["Tools"]   else []
    bd.mcp_tools = [CategoryItem(name="MCP",     tokens=scale(totals["MCP"]))]     if totals["MCP"]     else []
    bd.agents    = [CategoryItem(name="Agents",  tokens=scale(totals["Agents"]))]  if totals["Agents"]  else []
    bd.messages_tokens  = scale(totals["Messages"])
    bd.reasoning_tokens = scale(totals.get("Reasoning", 0))

    attributed = (
        sum(i.tokens for i in bd.skills)
        + sum(i.tokens for i in bd.tools)
        + sum(i.tokens for i in bd.mcp_tools)
        + sum(i.tokens for i in bd.agents)
        + bd.messages_tokens
        + bd.reasoning_tokens
    )
    bd.other_tokens = invisible_overhead + max(0, visible_token_estimate - attributed)
    return bd


def _classify_text_block(text: str, is_last_text: bool) -> str:
    first_line = text.split("\n", 1)[0]
    if first_line.startswith("Base directory") and "/skills/" in first_line:
        return "Skills"
    if "<system-reminder>" in text:
        return "Tools"
    if is_last_text:
        return "Messages"
    return "Other"


def classify_user_blocks(content, tool_name_by_id: dict) -> dict:
    counts = _empty_counts()
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
    counts = _empty_counts()
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
        elif btype == "thinking":
            # Extended thinking: the signature is an opaque blob that still occupies context.
            counts["Reasoning"] += len(block.get("thinking", "")) + len(block.get("signature", ""))
        elif btype == "redacted_thinking":
            counts["Reasoning"] += len(block.get("data", ""))
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


def categorize_exchange(human_content, intermediate_pairs, prior_assistant_content, fresh_tokens: int) -> CategoryBreakdown:
    tool_name_by_id: dict = {}
    if isinstance(prior_assistant_content, list):
        for block in prior_assistant_content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_name_by_id[block["id"]] = block.get("name", "")

    totals = _empty_counts()

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

    return _build_breakdown(totals, fresh_tokens)


def _codex_tool_category(name: str) -> str:
    """Bucket a Codex tool name into Tools / MCP / Agents."""
    if name.startswith("mcp__") or "__" in name:
        return "MCP"
    if name.lower() in CODEX_AGENT_TOOLS:
        return "Agents"
    return "Tools"


def _codex_item_text(payload: dict) -> str:
    """Flatten a Codex `content` / `output` list (or string) to plain text."""
    content = payload.get("content", payload.get("output", ""))
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
        )
    return ""


def classify_codex_items(items: list[dict], tool_name_by_call_id: dict) -> dict:
    """Char-count Codex rollout payloads into the shared category buckets.

    `items` are the raw `payload` dicts of the `response_item` / `event_msg`
    lines belonging to one exchange. Codex has no skill injection, so Skills
    always stays 0.
    """
    counts = _empty_counts()
    for payload in items:
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")

        if ptype == "user_message":
            counts["Messages"] += len(payload.get("message", "") or "")
        elif ptype == "agent_message":
            counts["Messages"] += len(payload.get("message", "") or "")
        elif ptype == "message":
            role = payload.get("role", "")
            text = _codex_item_text(payload)
            if role in ("user", "assistant"):
                counts["Messages"] += len(text)
            else:
                # developer / system instructions are injected overhead
                counts["Other"] += len(text)
        elif ptype == "reasoning":
            summary = payload.get("summary", []) or []
            chars = len(payload.get("encrypted_content", "") or "")
            for block in summary:
                if isinstance(block, dict):
                    chars += len(block.get("text", ""))
                elif isinstance(block, str):
                    chars += len(block)
            counts["Reasoning"] += chars
        elif ptype in ("custom_tool_call", "function_call"):
            name = payload.get("name", "")
            raw_input = payload.get("input", payload.get("arguments", ""))
            if not isinstance(raw_input, str):
                raw_input = _json.dumps(raw_input)
            counts[_codex_tool_category(name)] += len(name) + len(raw_input)
        elif ptype in ("custom_tool_call_output", "function_call_output"):
            name = tool_name_by_call_id.get(payload.get("call_id", ""), "")
            counts[_codex_tool_category(name)] += len(_codex_item_text(payload))
    return counts


def categorize_codex_exchange(items: list[dict], fresh_tokens: int) -> CategoryBreakdown:
    """Codex counterpart of `categorize_exchange`.

    Reuses the same char-count → scale → "Other" remainder machinery; only the
    per-item classifier differs.
    """
    tool_name_by_call_id: dict = {}
    for payload in items:
        if isinstance(payload, dict) and payload.get("type") in ("custom_tool_call", "function_call"):
            call_id = payload.get("call_id", "")
            if call_id:
                tool_name_by_call_id[call_id] = payload.get("name", "")

    return _build_breakdown(classify_codex_items(items, tool_name_by_call_id), fresh_tokens)
