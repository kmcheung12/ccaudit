import json
from pathlib import Path
from parser.codex_loader import list_codex_sessions, load_codex_session


def write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines))


def usage(inp=0, cached=0, write=0, out=0, reasoning=0) -> dict:
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": write,
        "output_tokens": out,
        "reasoning_output_tokens": reasoning,
        "total_tokens": inp + out,
    }


def token_count(ts: str, total: dict) -> dict:
    return {
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "token_count", "info": {"total_token_usage": total}},
    }


def session_meta(cwd: str, session_id: str = "aabbccdd-1111-2222-3333-444455556666") -> dict:
    return {
        "timestamp": "2026-01-01T00:00:00.000Z",
        "type": "session_meta",
        "payload": {"session_id": session_id, "cwd": cwd, "timestamp": "2026-01-01T00:00:00.000Z"},
    }


def turn_context(model: str) -> dict:
    return {"timestamp": "2026-01-01T00:00:01.000Z", "type": "turn_context",
            "payload": {"model": model, "cwd": "/Users/alan/code/proj"}}


def user_message(ts: str, text: str) -> dict:
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "user_message", "message": text}}


def agent_message(ts: str, text: str) -> dict:
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "agent_message", "message": text}}


def basic_rollout() -> list[dict]:
    """Two exchanges, a duplicate token_count, and a compaction before the second."""
    return [
        session_meta("/Users/alan/code/proj"),
        turn_context("gpt-5.6-sol"),
        user_message("2026-01-01T00:00:02.000Z", "first question"),
        {"timestamp": "2026-01-01T00:00:03.000Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command", "call_id": "c1",
                     "arguments": json.dumps({"cmd": "sed -n '1,20p' src/app/main.py"})}},
        {"timestamp": "2026-01-01T00:00:04.000Z", "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "c1",
                     "output": [{"type": "input_text", "text": "file body " * 20}]}},
        agent_message("2026-01-01T00:00:05.000Z", "here is the answer"),
        token_count("2026-01-01T00:00:06.000Z", usage(inp=1000, cached=400, write=50, out=200, reasoning=80)),
        # Duplicate emission of the same cumulative snapshot — must be ignored
        token_count("2026-01-01T00:00:07.000Z", usage(inp=1000, cached=400, write=50, out=200, reasoning=80)),
        {"timestamp": "2026-01-01T00:00:08.000Z", "type": "event_msg",
         "payload": {"type": "context_compacted"}},
        turn_context("gpt-5.6-codex"),
        user_message("2026-01-01T00:00:09.000Z", "second question"),
        {"timestamp": "2026-01-01T00:00:10.000Z", "type": "response_item",
         "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking hard"}],
                     "encrypted_content": "x" * 500}},
        agent_message("2026-01-01T00:00:11.000Z", "second answer"),
        token_count("2026-01-01T00:00:12.000Z", usage(inp=3000, cached=1400, write=90, out=500, reasoning=300)),
    ]


# load_codex_session


def test_load_codex_session_empty_file(tmp_path):
    f = tmp_path / "rollout-empty.jsonl"
    f.write_text("")
    session = load_codex_session(f)
    assert session.session_id == "rollout-empty"
    assert session.exchanges == []


def test_load_codex_session_skips_malformed_lines(tmp_path):
    f = tmp_path / "rollout-bad.jsonl"
    f.write_text("not json\n{also bad\n")
    assert load_codex_session(f).exchanges == []


def test_load_codex_session_session_id_from_meta(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    session = load_codex_session(f)
    assert session.session_id == "aabbccdd-1111-2222-3333-444455556666"
    assert session.display_name == "aabbccdd"
    assert session.jsonl_path == str(f)


def meta_with(**fields) -> dict:
    """A session_meta line whose payload carries extra identity fields."""
    line = session_meta("/Users/alan/code/proj")
    line["payload"].update(fields)
    return line


def load_meta(tmp_path, **fields):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, [meta_with(**fields)])
    return load_codex_session(f)


def test_load_codex_session_prefers_unique_id_over_session_id(tmp_path):
    # session_id is a thread-group id shared across rollouts; id is per-file
    session = load_meta(tmp_path, id="01a0614d-c81b-7393-931c-0f0f61a42faa")
    assert session.session_id == "01a0614d-c81b-7393-931c-0f0f61a42faa"
    assert session.display_name == "01a0614d"


def test_load_codex_session_falls_back_to_filename_stem(tmp_path):
    f = tmp_path / "rollout-nometa.jsonl"
    write_jsonl(f, [{"timestamp": "t", "type": "session_meta", "payload": {"cwd": "/x"}}])
    session = load_codex_session(f)
    assert session.session_id == "rollout-nometa"
    assert session.display_name == "rollout-"


def test_load_codex_session_subagent_nested_source_label(tmp_path):
    session = load_meta(tmp_path, id="01a0614d-c81b-7393-931c-0f0f61a42faa",
                        thread_source="subagent", source={"subagent": {"other": "guardian"}})
    assert session.display_name == "01a0614d ⤷guardian"


def test_load_codex_session_subagent_string_source_label(tmp_path):
    session = load_meta(tmp_path, id="01a0614d-c81b-7393-931c-0f0f61a42faa",
                        thread_source="subagent", source={"subagent": "review"})
    assert session.display_name == "01a0614d ⤷review"


def test_load_codex_session_user_thread_source_cli(tmp_path):
    session = load_meta(tmp_path, id="01a0614d-c7c6-7f13-a0eb-8321c64c2d22",
                        thread_source="user", source="cli")
    assert session.display_name == "01a0614d"


def test_load_codex_session_unknown_subagent_source_shapes(tmp_path):
    for source in ("cli", None, {}, {"subagent": None}, {"subagent": {}}, ["weird"], 7):
        session = load_meta(tmp_path, id="01a0614d-c81b-7393-931c-0f0f61a42faa",
                            thread_source="subagent", source=source)
        assert session.display_name == "01a0614d ⤷subagent"


def test_load_codex_session_missing_source_key(tmp_path):
    session = load_meta(tmp_path, id="01a0614d-c81b-7393-931c-0f0f61a42faa",
                        thread_source="subagent")
    assert session.display_name == "01a0614d ⤷subagent"


def test_load_codex_session_exchange_boundaries(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    session = load_codex_session(f)
    assert len(session.exchanges) == 2
    assert [e.exchange_number for e in session.exchanges] == [1, 2]
    assert session.exchanges[0].user_text == "first question"
    assert session.exchanges[1].user_text == "second question"
    assert session.exchanges[0].assistant_text == "here is the answer"


def test_load_codex_session_input_excludes_cached(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    ex = load_codex_session(f).exchanges[0]
    # Codex input_tokens includes cached; fresh input is the difference
    assert ex.input_tokens == 600
    assert ex.cache_read_tokens == 400
    assert ex.cache_create_tokens == 50
    assert ex.output_tokens == 200
    assert ex.reasoning_output_tokens == 80


def test_load_codex_session_uses_cumulative_deltas(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    ex = load_codex_session(f).exchanges[1]
    # Second snapshot minus the first, not the raw cumulative figures
    assert ex.input_tokens == (3000 - 1000) - (1400 - 400)
    assert ex.cache_read_tokens == 1000
    assert ex.cache_create_tokens == 40
    assert ex.output_tokens == 300
    assert ex.reasoning_output_tokens == 220


def test_load_codex_session_totals_reconcile_with_final_snapshot(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    session = load_codex_session(f)
    assert session.total_input_tokens + session.total_cache_read_tokens == 3000
    assert session.total_cache_create_tokens == 90
    assert session.total_output_tokens == 500
    assert session.total_reasoning_output_tokens == 300


def test_load_codex_session_duplicate_token_count_ignored(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    # The duplicate would double exchange 1 if summed naively
    assert load_codex_session(f).exchanges[0].output_tokens == 200


def test_load_codex_session_model_from_turn_context(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    exchanges = load_codex_session(f).exchanges
    assert exchanges[0].model == "gpt-5.6-sol"
    assert exchanges[1].model == "gpt-5.6-codex"


def test_load_codex_session_compaction_flags_next_exchange(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    exchanges = load_codex_session(f).exchanges
    assert exchanges[0].after_compact is False
    assert exchanges[1].after_compact is True


def test_load_codex_session_top_level_compacted_line(tmp_path):
    lines = basic_rollout()
    lines[8] = {"timestamp": "2026-01-01T00:00:08.000Z", "type": "compacted",
                "payload": {"message": "summary", "window_number": 2}}
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, lines)
    assert load_codex_session(f).exchanges[1].after_compact is True


def test_load_codex_session_tool_calls_and_files(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    ex = load_codex_session(f).exchanges[0]
    assert ex.tool_calls == [("exec_command", {"cmd": "sed -n '1,20p' src/app/main.py"})]
    assert ex.files_read == ["src/app/main.py"]


def test_load_codex_session_non_json_tool_input_is_wrapped(tmp_path):
    lines = basic_rollout()
    lines[3] = {"timestamp": "2026-01-01T00:00:03.000Z", "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                            "input": "tools.exec_command({cmd:\"cat src/app/main.py\"})"}}
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, lines)
    ex = load_codex_session(f).exchanges[0]
    name, inp = ex.tool_calls[0]
    assert name == "exec"
    assert inp["input"].startswith("tools.exec_command")
    assert ex.files_read == ["src/app/main.py"]


def test_load_codex_session_reasoning_is_categorized(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    ex = load_codex_session(f).exchanges[1]
    assert ex.category_breakdown.category_totals()["Reasoning"] > 0
    assert ex.category_breakdown.category_totals()["Skills"] == 0


def test_load_codex_session_timestamps_and_lines(tmp_path):
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, basic_rollout())
    ex = load_codex_session(f).exchanges[0]
    assert ex.first_timestamp == "2026-01-01T00:00:02.000Z"
    assert ex.timestamp == "2026-01-01T00:00:06.000Z"
    assert ex.duration_seconds == 4.0
    assert ex.jsonl_line_start == 3
    assert ex.jsonl_line_end >= ex.jsonl_line_start


def test_load_codex_session_usage_before_first_user_message(tmp_path):
    lines = [
        session_meta("/Users/alan/code/proj"),
        token_count("2026-01-01T00:00:01.000Z", usage(inp=100, cached=0, out=10)),
        user_message("2026-01-01T00:00:02.000Z", "hi"),
        token_count("2026-01-01T00:00:03.000Z", usage(inp=300, cached=0, out=30)),
    ]
    f = tmp_path / "rollout-x.jsonl"
    write_jsonl(f, lines)
    session = load_codex_session(f)
    # The pre-amble delta is carried into the first exchange, so nothing is lost
    assert len(session.exchanges) == 1
    assert session.exchanges[0].input_tokens == 300
    assert session.exchanges[0].output_tokens == 30


# list_codex_sessions


def test_list_codex_sessions_keys_by_cwd_slug(tmp_path):
    day = tmp_path / "2026" / "01" / "01"
    day.mkdir(parents=True)
    f = day / "rollout-2026-01-01T00-00-00-abc.jsonl"
    write_jsonl(f, basic_rollout())
    assert list_codex_sessions(tmp_path) == {"-Users-alan-code-proj": [f]}


def test_list_codex_sessions_missing_dir(tmp_path):
    assert list_codex_sessions(tmp_path / "nope") == {}


def test_list_codex_sessions_skips_unusable_files(tmp_path):
    day = tmp_path / "2026" / "01" / "01"
    day.mkdir(parents=True)
    (day / "rollout-empty.jsonl").write_text("")
    (day / "rollout-bad.jsonl").write_text("{not json\n")
    write_jsonl(day / "rollout-nocwd.jsonl", [{"type": "session_meta", "payload": {"session_id": "x"}}])
    assert list_codex_sessions(tmp_path) == {}


def test_list_codex_sessions_groups_multiple_files(tmp_path):
    day = tmp_path / "2026" / "01" / "01"
    day.mkdir(parents=True)
    for name in ("rollout-a.jsonl", "rollout-b.jsonl"):
        write_jsonl(day / name, [session_meta("/Users/alan/code/proj")])
    write_jsonl(day / "rollout-c.jsonl", [session_meta("/Users/alan/code/other")])
    result = list_codex_sessions(tmp_path)
    assert len(result["-Users-alan-code-proj"]) == 2
    assert len(result["-Users-alan-code-other"]) == 1
