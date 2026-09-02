import json
import pytest
from pathlib import Path
from parser.loader import slug_to_display, load_session, list_projects, load_project
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
    assert session.exchanges == []


def test_load_session_skips_malformed_lines(tmp_path):
    f = tmp_path / "abc123.jsonl"
    f.write_text("not json\n{also bad\n")
    session = load_session(f)
    assert session.exchanges == []


def test_load_session_extracts_exchanges(tmp_path):
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
    assert len(session.exchanges) == 1
    exchange = session.exchanges[0]
    assert exchange.input_tokens == 5
    assert exchange.cache_read_tokens == 9550
    assert exchange.cache_create_tokens == 100
    assert exchange.output_tokens == 20
    assert exchange.exchange_number == 1


def test_load_session_display_name(tmp_path):
    f = tmp_path / "4b177c76-c056-4ed7-b62f-1d41710cf376.jsonl"
    f.write_text("")
    session = load_session(f)
    assert session.display_name == "4b177c76"


def test_load_session_multiple_exchanges(tmp_path):
    f = tmp_path / "multi.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "msg1"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50, "output_tokens": 5}}, "timestamp": "2026-01-01T00:00:01Z"},
        {"type": "user", "message": {"content": "msg2"}, "timestamp": "2026-01-01T00:00:02Z"},
        {"type": "assistant", "message": {"usage": {"input_tokens": 8, "cache_read_input_tokens": 200, "cache_creation_input_tokens": 30, "output_tokens": 12}}, "timestamp": "2026-01-01T00:00:03Z"},
    ])
    session = load_session(f)
    assert len(session.exchanges) == 2
    assert session.exchanges[0].exchange_number == 1
    assert session.exchanges[1].exchange_number == 2


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
    assert len(session.exchanges) == 2
    assert not session.exchanges[0].after_compact
    assert session.exchanges[1].after_compact


def test_load_session_skips_assistant_without_usage(tmp_path):
    f = tmp_path / "nousage.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "hi"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant"}, "timestamp": "2026-01-01T00:00:01Z"},
    ])
    session = load_session(f)
    assert session.exchanges == []


# list_projects

def write_rollout(sessions_dir: Path, name: str, cwd: str, lines: list[dict] | None = None) -> Path:
    """Write a minimal Codex rollout file under sessions_dir/2026/01/01/."""
    day_dir = sessions_dir / "2026" / "01" / "01"
    day_dir.mkdir(parents=True, exist_ok=True)
    f = day_dir / f"rollout-{name}.jsonl"
    records = [{
        "timestamp": "2026-01-01T00:00:00Z",
        "type": "session_meta",
        "payload": {"session_id": name, "cwd": cwd},
    }]
    records.extend(lines or [])
    write_jsonl(f, records)
    return f


def test_list_projects(tmp_path):
    (tmp_path / "-Users-alice-code-alpha").mkdir()
    (tmp_path / "-Users-alice-code-beta").mkdir()
    projects = list_projects(tmp_path, codex_sessions_dir=tmp_path / "nope")
    names = [p.display_name for p in projects]
    assert "alpha" in names
    assert "beta" in names
    for p in projects:
        assert not p.loaded
        assert p.claude_dir == str(tmp_path / p.project_slug)
        assert p.codex_files == []


def test_list_projects_merges_codex_into_matching_claude_project(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "-Users-alice-code-alpha").mkdir()
    sessions_dir = tmp_path / "codex"
    rollout = write_rollout(sessions_dir, "r1", "/Users/alice/code/alpha")

    projects = list_projects(projects_dir, codex_sessions_dir=sessions_dir)
    assert len(projects) == 1
    project = projects[0]
    assert project.project_slug == "-Users-alice-code-alpha"
    assert project.claude_dir == str(projects_dir / "-Users-alice-code-alpha")
    assert project.codex_files == [str(rollout)]


def test_list_projects_adds_codex_only_project(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "-Users-alice-code-alpha").mkdir()
    sessions_dir = tmp_path / "codex"
    rollout = write_rollout(sessions_dir, "r1", "/Users/alice/code/zeta")

    projects = list_projects(projects_dir, codex_sessions_dir=sessions_dir)
    assert [p.project_slug for p in projects] == [
        "-Users-alice-code-alpha", "-Users-alice-code-zeta",
    ]
    codex_only = projects[1]
    assert codex_only.claude_dir is None
    assert codex_only.display_name == "zeta"
    assert codex_only.codex_files == [str(rollout)]


def test_list_projects_include_flags(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "-Users-alice-code-alpha").mkdir()
    sessions_dir = tmp_path / "codex"
    write_rollout(sessions_dir, "r1", "/Users/alice/code/zeta")

    claude_only = list_projects(projects_dir, include_codex=False, codex_sessions_dir=sessions_dir)
    assert [p.project_slug for p in claude_only] == ["-Users-alice-code-alpha"]
    assert claude_only[0].codex_files == []

    codex_only = list_projects(projects_dir, include_claude=False, codex_sessions_dir=sessions_dir)
    assert [p.project_slug for p in codex_only] == ["-Users-alice-code-zeta"]
    assert codex_only[0].claude_dir is None

    neither = list_projects(projects_dir, include_claude=False, include_codex=False,
                            codex_sessions_dir=sessions_dir)
    assert neither == []


# load_project

def test_load_project_uses_claude_dir(tmp_path):
    project_dir = tmp_path / "-Users-alice-code-alpha"
    project_dir.mkdir()
    write_jsonl(project_dir / "sess1.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant", "content": "yo",
                                          "usage": {"input_tokens": 10, "output_tokens": 5}},
         "timestamp": "2026-01-01T00:00:01Z"},
    ])
    project = ProjectStats(project_slug=project_dir.name, display_name="alpha",
                           claude_dir=str(project_dir))
    load_project(project)
    assert project.loaded
    assert project.load_error is None
    assert [s.session_id for s in project.sessions] == ["sess1"]


def _codex_lines() -> list[dict]:
    return [
        {"timestamp": "2026-01-01T00:00:01Z", "type": "turn_context",
         "payload": {"model": "gpt-5.6-sol"}},
        {"timestamp": "2026-01-01T00:00:02Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "hi from codex"}},
        {"timestamp": "2026-01-01T00:00:03Z", "type": "event_msg",
         "payload": {"type": "token_count", "info": {"total_token_usage": {
             "input_tokens": 100, "cached_input_tokens": 40,
             "cache_write_input_tokens": 10, "output_tokens": 20,
             "reasoning_output_tokens": 5}}}},
    ]


def test_load_project_loads_both_sources(tmp_path):
    project_dir = tmp_path / "-Users-alice-code-alpha"
    project_dir.mkdir()
    write_jsonl(project_dir / "sess1.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant", "content": "yo",
                                          "usage": {"input_tokens": 10, "output_tokens": 5}},
         "timestamp": "2026-01-01T00:00:01Z"},
    ])
    rollout = write_rollout(tmp_path / "codex", "r1", "/Users/alice/code/alpha", _codex_lines())

    project = ProjectStats(project_slug=project_dir.name, display_name="alpha",
                           claude_dir=str(project_dir), codex_files=[str(rollout)])
    load_project(project)
    assert project.loaded
    assert project.load_error is None
    assert [s.session_id for s in project.sessions] == ["sess1", "r1"]
    codex_session = project.sessions[1]
    assert len(codex_session.exchanges) == 1
    assert codex_session.exchanges[0].model == "gpt-5.6-sol"
    assert codex_session.exchanges[0].input_tokens == 60   # 100 - 40 cached
    assert codex_session.exchanges[0].reasoning_output_tokens == 5


def test_load_project_codex_only(tmp_path):
    rollout = write_rollout(tmp_path / "codex", "r1", "/Users/alice/code/zeta", _codex_lines())
    project = ProjectStats(project_slug="-Users-alice-code-zeta", display_name="zeta",
                           codex_files=[str(rollout)])
    load_project(project)
    assert project.loaded
    assert project.load_error is None
    assert [s.session_id for s in project.sessions] == ["r1"]


def test_load_project_bad_codex_file_does_not_drop_claude_sessions(tmp_path):
    project_dir = tmp_path / "-Users-alice-code-alpha"
    project_dir.mkdir()
    write_jsonl(project_dir / "sess1.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant", "content": "yo",
                                          "usage": {"input_tokens": 10, "output_tokens": 5}},
         "timestamp": "2026-01-01T00:00:01Z"},
    ])
    project = ProjectStats(project_slug=project_dir.name, display_name="alpha",
                           claude_dir=str(project_dir),
                           codex_files=[str(tmp_path / "missing" / "rollout-x.jsonl")])
    load_project(project)
    assert project.loaded
    assert [s.session_id for s in project.sessions] == ["sess1"]
    assert "rollout-x.jsonl" in project.load_error


def test_load_project_without_claude_dir_is_a_noop():
    project = ProjectStats(project_slug="-Users-alice-code-beta", display_name="beta")
    load_project(project)
    assert project.loaded
    assert project.load_error is None
    assert project.sessions == []


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


# _group_exchanges

from parser.loader import _group_exchanges

def _msg(type_, role=None, content=None, usage=None):
    m = {"type": type_}
    if role:
        m["message"] = {"role": role, "content": content or [], "usage": usage}
    return m

def test_single_exchange_no_tools():
    msgs = [
        _msg("user",      "user",      [{"type": "text", "text": "hi"}]),
        _msg("assistant", "assistant", [{"type": "text", "text": "hello"}],
             usage={"input_tokens": 10, "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0, "output_tokens": 5}),
    ]
    exchanges = _group_exchanges(msgs)
    assert len(exchanges) == 1
    assert len(exchanges[0]["assistant_msgs"]) == 1
    assert len(exchanges[0]["intermediate_pairs"]) == 0

def test_exchange_with_one_tool_roundtrip():
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
    exchanges = _group_exchanges(msgs)
    assert len(exchanges) == 1
    assert len(exchanges[0]["intermediate_pairs"]) == 1
    assert len(exchanges[0]["assistant_msgs"]) == 2

def test_cache_create_5m_1h_parsed(tmp_path):
    f = tmp_path / "cache_ttl.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "hi"}, "timestamp": "2026-01-01T00:00:00Z"},
        {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 9500,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 1000,
                        "ephemeral_1h_input_tokens": 8500,
                    },
                    "output_tokens": 5,
                },
            },
            "timestamp": "2026-01-01T00:00:01Z",
        },
    ])
    session = load_session(f)
    exchange = session.exchanges[0]
    assert exchange.cache_create_tokens == 9500
    assert exchange.cache_create_5m_tokens == 1000
    assert exchange.cache_create_1h_tokens == 8500
    assert exchange.cache_create_5m_tokens + exchange.cache_create_1h_tokens == exchange.cache_create_tokens


def test_cache_create_5m_1h_missing_cache_creation_field(tmp_path):
    """Older messages without the cache_creation sub-object default to zero."""
    f = tmp_path / "no_cache_creation.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "hi"}, "timestamp": "2026-01-01T00:00:00Z"},
        {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 500,
                    "output_tokens": 5,
                },
            },
            "timestamp": "2026-01-01T00:00:01Z",
        },
    ])
    session = load_session(f)
    exchange = session.exchanges[0]
    assert exchange.cache_create_tokens == 500
    assert exchange.cache_create_5m_tokens == 0
    assert exchange.cache_create_1h_tokens == 0


def test_cache_create_5m_1h_summed_across_tool_roundtrips(tmp_path):
    """5m/1h tokens accumulate across all assistant messages in a turn."""
    f = tmp_path / "multi_asst.jsonl"
    write_jsonl(f, [
        {"type": "user", "message": {"content": "go"}, "timestamp": "2026-01-01T00:00:00Z"},
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                "usage": {
                    "input_tokens": 5,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 300,
                    "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200},
                    "output_tokens": 2,
                },
            },
            "timestamp": "2026-01-01T00:00:01Z",
        },
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "data"}]}, "timestamp": "2026-01-01T00:00:02Z"},
        {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 3,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 700,
                    "cache_creation": {"ephemeral_5m_input_tokens": 400, "ephemeral_1h_input_tokens": 300},
                    "output_tokens": 4,
                },
            },
            "timestamp": "2026-01-01T00:00:03Z",
        },
    ])
    session = load_session(f)
    assert len(session.exchanges) == 1
    exchange = session.exchanges[0]
    assert exchange.cache_create_5m_tokens == 500   # 100 + 400
    assert exchange.cache_create_1h_tokens == 500   # 200 + 300
    assert exchange.cache_create_tokens == 1000     # 300 + 700


def test_two_human_exchanges():
    usage = {"input_tokens": 5, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 0, "output_tokens": 2}
    msgs = [
        _msg("user",      "user",      [{"type": "text", "text": "first"}]),
        _msg("assistant", "assistant", [{"type": "text", "text": "reply"}], usage=usage),
        _msg("user",      "user",      [{"type": "text", "text": "second"}]),
        _msg("assistant", "assistant", [{"type": "text", "text": "reply2"}], usage=usage),
    ]
    exchanges = _group_exchanges(msgs)
    assert len(exchanges) == 2
