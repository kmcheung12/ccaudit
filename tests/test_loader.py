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


# _is_human_user_message

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


# _group_turns

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
