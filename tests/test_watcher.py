# tests/test_watcher.py
from pathlib import Path
from parser.loader import load_session, apply_session_updates
import tempfile, json, os

def _write_jsonl(path: Path, messages: list[dict]) -> None:
    with open(path, "w") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")

def _minimal_session_messages() -> list[dict]:
    return [
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "timestamp": "2026-04-14T10:00:00Z",
            "uuid": "aaa",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "hi",
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0, "output_tokens": 5},
            },
            "timestamp": "2026-04-14T10:00:01Z",
            "uuid": "bbb",
        },
    ]

def test_session_jsonl_path_is_set():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "abc123.jsonl"
        _write_jsonl(p, _minimal_session_messages())
        session = load_session(p)
        assert session.jsonl_path == str(p)

def test_apply_session_updates_appends_new_exchanges():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "abc123.jsonl"
        msgs = _minimal_session_messages()
        _write_jsonl(p, msgs)
        session = load_session(p)
        assert len(session.exchanges) == 1

        # Add a second exchange to the file
        second = [
            {
                "type": "user",
                "message": {"role": "user", "content": "again"},
                "timestamp": "2026-04-14T10:01:00Z",
                "uuid": "ccc",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "sure",
                    "usage": {"input_tokens": 8, "cache_read_input_tokens": 0,
                              "cache_creation_input_tokens": 0, "output_tokens": 3},
                },
                "timestamp": "2026-04-14T10:01:01Z",
                "uuid": "ddd",
            },
        ]
        _write_jsonl(p, msgs + second)
        updated = load_session(p)
        added = apply_session_updates(session, updated)
        assert added == 1
        assert len(session.exchanges) == 2

def test_apply_session_updates_no_change():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "abc123.jsonl"
        _write_jsonl(p, _minimal_session_messages())
        session = load_session(p)
        updated = load_session(p)
        added = apply_session_updates(session, updated)
        assert added == 0
        assert len(session.exchanges) == 1

from parser.models import GlobalStats, ProjectStats, SessionStats as _SessionStats
from tui.watcher import latest_jsonl_path, find_session_by_path

def _make_loaded_project(sessions: list) -> ProjectStats:
    p = ProjectStats(project_slug="test", display_name="test")
    p.sessions = sessions
    p.loaded = True
    return p

def test_latest_jsonl_path_returns_most_recent(tmp_path):
    a = tmp_path / "aaa.jsonl"
    b = tmp_path / "bbb.jsonl"
    a.write_text("")
    b.write_text("")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))

    s_a = _SessionStats(session_id="aaa", display_name="aaa", first_timestamp=None, jsonl_path=str(a))
    s_b = _SessionStats(session_id="bbb", display_name="bbb", first_timestamp=None, jsonl_path=str(b))
    project = _make_loaded_project([s_a, s_b])

    result = latest_jsonl_path([project])
    assert result == str(b)

def test_latest_jsonl_path_skips_unloaded():
    project = ProjectStats(project_slug="x", display_name="x")
    project.loaded = False
    assert latest_jsonl_path([project]) is None

def test_find_session_by_path():
    s = _SessionStats(session_id="aaa", display_name="aaa", first_timestamp=None, jsonl_path="/tmp/aaa.jsonl")
    project = _make_loaded_project([s])

    found_project, found_session = find_session_by_path([project], "/tmp/aaa.jsonl")
    assert found_session is s
    assert found_project is project

def test_find_session_by_path_not_found():
    p, s = find_session_by_path([], "/tmp/nope.jsonl")
    assert p is None
    assert s is None
