import json
from parser.categorizer import (
    classify_user_blocks,
    classify_assistant_blocks,
    classify_codex_items,
    categorize_exchange,
    categorize_codex_exchange,
)
from parser.models import CategoryBreakdown


# classify_user_blocks

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

def test_string_content_is_messages():
    result = classify_user_blocks("plain string message", tool_name_by_id={})
    assert result["Messages"] > 0


# classify_assistant_blocks

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


# categorize_exchange

def test_categorize_exchange_attributes_tool_result_to_tools():
    human_content = [
        {"type": "tool_result", "tool_use_id": "t1", "content": "file content " * 50},
        {"type": "text", "text": "thanks"},
    ]
    prior_assistant_content = [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/foo.py"}},
    ]
    bd = categorize_exchange(
        human_content=human_content,
        intermediate_pairs=[],
        prior_assistant_content=prior_assistant_content,
        fresh_tokens=1000,
    )
    assert isinstance(bd, CategoryBreakdown)
    tools_total = sum(i.tokens for i in bd.tools)
    assert tools_total > 0
    assert bd.messages_tokens > 0

def test_categorize_exchange_budget_sums_to_fresh_tokens():
    human_content = [{"type": "text", "text": "hello world"}]
    bd = categorize_exchange(
        human_content=human_content,
        intermediate_pairs=[],
        prior_assistant_content=[],
        fresh_tokens=500,
    )
    total = sum(bd.category_totals().values())
    assert total == 500

def test_categorize_exchange_mcp_attributed():
    human_content = [{"type": "text", "text": "check slack"}]
    intermediate_pairs = [
        (
            [{"type": "tool_use", "id": "t1", "name": "mcp__slack__read_channel",
              "input": {"channel": "general"}}],
            [{"type": "tool_result", "tool_use_id": "t1", "content": "some messages " * 20}],
        )
    ]
    bd = categorize_exchange(
        human_content=human_content,
        intermediate_pairs=intermediate_pairs,
        prior_assistant_content=[],
        fresh_tokens=1000,
    )
    mcp_total = sum(i.tokens for i in bd.mcp_tools)
    tools_total = sum(i.tokens for i in bd.tools)
    assert mcp_total > 0
    assert tools_total == 0  # tool_result of MCP call → MCP, not Tools
    assert sum(bd.category_totals().values()) == 1000


# Reasoning — Claude thinking blocks

def test_thinking_block_is_reasoning():
    content = [{"type": "thinking", "thinking": "let me work through this", "signature": "abc123"}]
    result = classify_assistant_blocks(content)
    assert result["Reasoning"] > 0
    assert result["Messages"] == 0
    assert result["Other"] == 0


def test_redacted_thinking_block_is_reasoning():
    content = [{"type": "redacted_thinking", "data": "opaque blob"}]
    assert classify_assistant_blocks(content)["Reasoning"] > 0


def test_categorize_exchange_attributes_thinking_to_reasoning():
    intermediate_pairs = [
        (
            [{"type": "thinking", "thinking": "deep thoughts " * 40, "signature": "sig"}],
            [{"type": "text", "text": "ok"}],
        )
    ]
    bd = categorize_exchange(
        human_content=[{"type": "text", "text": "solve this"}],
        intermediate_pairs=intermediate_pairs,
        prior_assistant_content=[],
        fresh_tokens=1000,
    )
    assert bd.reasoning_tokens > 0
    assert sum(bd.category_totals().values()) == 1000


# classify_codex_items

def test_codex_user_and_agent_messages_are_messages():
    items = [
        {"type": "user_message", "message": "please fix this bug"},
        {"type": "agent_message", "message": "done, here is the fix"},
    ]
    result = classify_codex_items(items, tool_name_by_call_id={})
    assert result["Messages"] > 0
    assert result["Other"] == 0


def test_codex_message_roles():
    items = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
        {"type": "message", "role": "developer",
         "content": [{"type": "input_text", "text": "<permissions instructions>"}]},
    ]
    result = classify_codex_items(items, tool_name_by_call_id={})
    assert result["Messages"] == len("hello") + len("hi")
    assert result["Other"] == len("<permissions instructions>")


def test_codex_reasoning_counts_summary_and_encrypted_content():
    items = [{"type": "reasoning",
              "summary": [{"type": "summary_text", "text": "abc"}],
              "encrypted_content": "x" * 100}]
    result = classify_codex_items(items, tool_name_by_call_id={})
    assert result["Reasoning"] == 103
    assert result["Skills"] == 0


def test_codex_tool_call_and_output_are_tools():
    items = [
        {"type": "custom_tool_call", "name": "exec", "call_id": "c1", "input": "ls -la"},
        {"type": "custom_tool_call_output", "call_id": "c1",
         "output": [{"type": "input_text", "text": "file listing " * 10}]},
    ]
    result = classify_codex_items(items, tool_name_by_call_id={"c1": "exec"})
    assert result["Tools"] > 0
    assert result["MCP"] == 0


def test_codex_mcp_tool_name():
    items = [{"type": "function_call", "name": "mcp__slack__read_channel", "call_id": "c1",
              "arguments": json.dumps({"channel": "general"})}]
    result = classify_codex_items(items, tool_name_by_call_id={})
    assert result["MCP"] > 0
    assert result["Tools"] == 0


def test_codex_agent_spawn_tool():
    items = [{"type": "function_call", "name": "spawn_agent", "call_id": "c1",
              "arguments": json.dumps({"prompt": "go research this"})}]
    result = classify_codex_items(items, tool_name_by_call_id={})
    assert result["Agents"] > 0
    assert result["Tools"] == 0


# categorize_codex_exchange

def test_categorize_codex_exchange_budget_sums_to_fresh_tokens():
    items = [{"type": "user_message", "message": "hello world"}]
    bd = categorize_codex_exchange(items, fresh_tokens=500)
    assert isinstance(bd, CategoryBreakdown)
    assert sum(bd.category_totals().values()) == 500


def test_categorize_codex_exchange_maps_output_to_calling_tool():
    items = [
        {"type": "user_message", "message": "check slack"},
        {"type": "function_call", "name": "mcp__slack__read_channel", "call_id": "c1",
         "arguments": json.dumps({"channel": "general"})},
        {"type": "function_call_output", "call_id": "c1",
         "output": [{"type": "input_text", "text": "some messages " * 20}]},
    ]
    bd = categorize_codex_exchange(items, fresh_tokens=1000)
    assert sum(i.tokens for i in bd.mcp_tools) > 0
    assert sum(i.tokens for i in bd.tools) == 0  # output of an MCP call → MCP
    assert sum(bd.category_totals().values()) == 1000


def test_categorize_codex_exchange_reasoning_and_no_skills():
    items = [
        {"type": "user_message", "message": "think about it"},
        {"type": "reasoning", "summary": [], "encrypted_content": "z" * 800},
    ]
    bd = categorize_codex_exchange(items, fresh_tokens=1000)
    assert bd.reasoning_tokens > 0
    assert bd.skills == []
    assert sum(bd.category_totals().values()) == 1000
