# tests/test_watcher.py
from pathlib import Path
from parser.loader import load_session
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
