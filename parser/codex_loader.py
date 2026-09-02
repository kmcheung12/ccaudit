from __future__ import annotations
import json
import re
from pathlib import Path
from parser.models import SessionStats, ExchangeStats
from parser import categorizer

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# Usage counters Codex reports in `payload.info.total_token_usage`.
_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)

# Path-ish tokens inside a shell command: at least one "/" and a file extension.
_PATH_RE = re.compile(r"(?:[\w.~-]+/)+[\w.-]+\.\w{1,6}")


def list_codex_sessions(sessions_dir: Path = CODEX_SESSIONS_DIR) -> dict[str, list[Path]]:
    """Map Claude project slug → Codex rollout files that ran in that directory.

    Only the first line of each rollout (the `session_meta` record) is read, so
    this stays cheap even with hundreds of multi-megabyte logs.
    """
    # Imported locally: parser.loader imports this module, so a module-level
    # import here would be circular.
    from parser.loader import path_to_slug

    if not sessions_dir.exists():
        return {}
    by_slug: dict[str, list[Path]] = {}
    for jsonl_file in sorted(sessions_dir.rglob("rollout-*.jsonl")):
        cwd = _read_session_cwd(jsonl_file)
        if not cwd:
            continue
        by_slug.setdefault(path_to_slug(Path(cwd)), []).append(jsonl_file)
    return by_slug


def _read_session_cwd(jsonl_file: Path) -> str:
    """Return `payload.cwd` from a rollout's session_meta line, or "" if unreadable."""
    try:
        with open(jsonl_file, encoding="utf-8") as f:
            first_line = f.readline().strip()
    except OSError:
        return ""
    if not first_line:
        return ""
    try:
        meta = json.loads(first_line)
    except json.JSONDecodeError:
        return ""
    payload = meta.get("payload", {})
    if not isinstance(payload, dict):
        return ""
    return payload.get("cwd", "") or ""


def _subagent_label(source) -> str:
    """Name of the subagent behind a `session_meta` payload's `source` field.

    Observed shapes are `{"subagent": {"other": "guardian"}}` and
    `{"subagent": "review"}`; anything else degrades to a bare "subagent".
    """
    if isinstance(source, dict):
        inner = source.get("subagent")
        if isinstance(inner, str) and inner:
            return inner
        if isinstance(inner, dict):
            for value in inner.values():
                if isinstance(value, str) and value:
                    return value
    return "subagent"


def _display_name(session_id: str, payload: dict) -> str:
    """Short tree label: the id prefix, plus the subagent name for subagent threads.

    `tui/tree.py` renders this in a 35-column pane, so it stays terse.
    """
    short = session_id[:8]
    if payload.get("thread_source") == "subagent":
        return f"{short} ⤷{_subagent_label(payload.get('source'))}"
    return short


def _usage_delta(snapshot: dict, cumulative: dict) -> dict:
    """Per-request usage = cumulative snapshot minus the previous snapshot.

    Codex re-emits `token_count` with an identical cumulative snapshot from time
    to time, so summing `last_token_usage` overcounts. Deltas of the cumulative
    total are exact by construction. Negative deltas are clamped defensively.
    """
    return {key: max(0, cumulative.get(key, 0) - snapshot.get(key, 0)) for key in _USAGE_KEYS}


def _is_zero_delta(delta: dict) -> bool:
    return not any(delta.values())


def _parse_tool_input(payload: dict) -> dict:
    """Normalise a Codex tool call's input into a dict for the detail pane.

    `custom_tool_call` carries a free-form `input` string, `function_call` a
    JSON `arguments` string. Anything that isn't a JSON object is wrapped rather
    than dropped so the call still renders.
    """
    if "input" in payload:
        raw, key = payload.get("input"), "input"
    else:
        raw, key = payload.get("arguments"), "arguments"
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {key: raw}
    return parsed if isinstance(parsed, dict) else {key: raw}


def _extract_paths(tool_input: dict) -> list[str]:
    """Best-effort file paths out of an `exec`-style tool input."""
    blob = " ".join(str(v) for v in tool_input.values() if isinstance(v, (str, int, float)))
    return list(dict.fromkeys(_PATH_RE.findall(blob)))  # dedupe, keep order


def _extract_assistant_text(items: list[dict]) -> str:
    """Text of the last `agent_message` in an exchange."""
    for payload in reversed(items):
        if payload.get("type") == "agent_message":
            return str(payload.get("message", ""))[:800]
    return ""


def _group_exchanges(raw_lines: list[dict]) -> list[dict]:
    """
    Group raw rollout lines into logical exchanges.

    Each exchange dict has keys:
        user_line     — the raw `event_msg/user_message` line that opened it
        items         — payloads of the response_item / event_msg lines inside it
        usage         — summed per-request usage deltas
        model         — `turn_context.payload.model` in effect when it opened
        after_compact — bool
        line_start / line_end / timestamp

    Usage is derived from deltas of the cumulative `total_token_usage`; deltas
    seen before the first user message are carried into the first exchange so no
    tokens are lost.
    """
    exchanges: list[dict] = []
    snapshot: dict = {key: 0 for key in _USAGE_KEYS}
    current_model = ""
    after_compact = False
    pending: dict | None = None
    pending_usage: dict = {key: 0 for key in _USAGE_KEYS}  # deltas seen before an exchange opened

    def _add_usage(target: dict, delta: dict) -> None:
        for key in _USAGE_KEYS:
            target[key] += delta[key]

    def _flush():
        nonlocal pending
        if pending is not None:
            exchanges.append(pending)
        pending = None

    for line in raw_lines:
        line_type = line.get("type")
        payload = line.get("payload", {})
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")

        if line_type == "turn_context":
            current_model = payload.get("model", "") or current_model
            continue

        if line_type == "compacted" or payload_type == "context_compacted":
            after_compact = True
            continue

        if payload_type == "user_message":
            # New exchange — flush the previous one first
            _flush()
            pending = {
                "user_line":     line,
                "items":         [payload],
                "usage":         dict(pending_usage),
                "model":         current_model,
                "after_compact": after_compact,
                "line_start":    line.get("_lineno", 0),
                "line_end":      line.get("_lineno", 0),
                "timestamp":     line.get("timestamp", ""),
            }
            pending_usage = {key: 0 for key in _USAGE_KEYS}
            after_compact = False
            continue

        if payload_type == "token_count":
            info = payload.get("info") or {}
            cumulative = info.get("total_token_usage") or {}
            if not cumulative:
                continue
            delta = _usage_delta(snapshot, cumulative)
            snapshot = {key: cumulative.get(key, 0) for key in _USAGE_KEYS}
            if _is_zero_delta(delta):
                continue  # duplicate token_count event
            _add_usage(pending["usage"] if pending else pending_usage, delta)
            if pending is not None:
                pending["line_end"] = line.get("_lineno", pending["line_end"])
                pending["timestamp"] = line.get("timestamp", pending["timestamp"])
            continue

        if pending is None:
            continue  # pre-amble before the first human message

        if line_type == "response_item" or payload_type == "agent_message":
            pending["items"].append(payload)
            pending["line_end"] = line.get("_lineno", pending["line_end"])
            pending["timestamp"] = line.get("timestamp", pending["timestamp"])

    # Flush the final open exchange
    _flush()
    return exchanges


def load_codex_session(jsonl_file: Path) -> SessionStats:
    """Parse a Codex rollout .jsonl file into a SessionStats."""
    raw_lines: list[dict] = []
    # `payload.session_id` is a thread-group id shared by every rollout of a
    # session (a user thread and its subagents), so it is not unique per file;
    # `payload.id` is. Fall back through session_id to the filename stem.
    session_id = jsonl_file.stem
    display_name = session_id[:8]
    with open(jsonl_file, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            record["_lineno"] = lineno
            if record.get("type") == "session_meta":
                payload = record.get("payload", {})
                if isinstance(payload, dict):
                    session_id = payload.get("id") or payload.get("session_id") or session_id
                    display_name = _display_name(session_id, payload)
            raw_lines.append(record)

    grouped = _group_exchanges(raw_lines)
    exchanges: list[ExchangeStats] = []

    for exchange_number, exchange in enumerate(grouped, start=1):
        usage = exchange["usage"]
        items = exchange["items"]

        # Codex's input_tokens INCLUDES cached input; Anthropic's does not.
        cache_read = usage["cached_input_tokens"]
        input_tokens = max(0, usage["input_tokens"] - cache_read)
        cache_create = usage["cache_write_input_tokens"]

        fresh_tokens = input_tokens + cache_create
        breakdown = categorizer.categorize_codex_exchange(items, fresh_tokens)

        files_read: list[str] = []
        tool_calls: list[tuple[str, dict]] = []
        for payload in items:
            if payload.get("type") not in ("custom_tool_call", "function_call"):
                continue
            name = payload.get("name", "")
            tool_input = _parse_tool_input(payload)
            tool_calls.append((name, tool_input))
            if name.startswith("exec"):
                files_read.extend(_extract_paths(tool_input))

        user_line = exchange["user_line"]
        user_text = str(user_line.get("payload", {}).get("message", ""))[:800]

        exchanges.append(ExchangeStats(
            exchange_number=exchange_number,
            timestamp=exchange["timestamp"],
            input_tokens=input_tokens,
            cache_read_tokens=cache_read,
            cache_create_tokens=cache_create,
            output_tokens=usage["output_tokens"],
            reasoning_output_tokens=usage["reasoning_output_tokens"],
            category_breakdown=breakdown,
            after_compact=exchange["after_compact"],
            user_text=user_text,
            assistant_text=_extract_assistant_text(items),
            files_read=files_read,
            tool_calls=tool_calls,
            raw_user=user_line,
            jsonl_path=str(jsonl_file),
            jsonl_line_start=exchange["line_start"],
            jsonl_line_end=exchange["line_end"],
            model=exchange["model"],
        ))

    return SessionStats(
        session_id=session_id,
        display_name=display_name,
        exchanges=exchanges,
        jsonl_path=str(jsonl_file),
    )
