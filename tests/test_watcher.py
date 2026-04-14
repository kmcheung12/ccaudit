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
