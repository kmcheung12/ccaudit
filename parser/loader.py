from __future__ import annotations
import json
from pathlib import Path
from parser.models import ProjectStats, SessionStats, ExchangeStats, CategoryBreakdown
from parser import categorizer

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def slug_to_display(slug: str) -> str:
    """Convert -Users-alan-code-my-project to my-project."""
    parts = slug.lstrip("-").split("-")
    if len(parts) >= 4:
        return "-".join(parts[3:])
    return slug


def list_projects(projects_dir: Path = PROJECTS_DIR) -> list[ProjectStats]:
    """Return unloaded ProjectStats for each subdirectory of projects_dir."""
    if not projects_dir.exists():
        return []
    projects = []
    for d in sorted(projects_dir.iterdir()):
        if d.is_dir():
            projects.append(ProjectStats(
                project_slug=d.name,
                display_name=slug_to_display(d.name),
            ))
    return projects


def path_to_slug(project_dir: Path) -> str:
    """Convert an absolute path to its Claude project slug.

    E.g. /Users/alan/code/ccaudit → -Users-alan-code-ccaudit
    """
    return project_dir.as_posix().replace("/", "-")


def load_project(project: ProjectStats, projects_dir: Path = PROJECTS_DIR) -> None:
    """Load all sessions for a project in-place. Sets project.loaded = True."""
    try:
        base = Path(project.projects_dir) if project.projects_dir else projects_dir
        project_dir = base / project.project_slug
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            project.sessions.append(load_session(jsonl_file))
        project.loaded = True
    except Exception as e:
        project.load_error = str(e)
        project.loaded = True


def _extract_text(content) -> str:
    """Flatten message content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    inner_text = ""
                    if isinstance(inner, list):
                        inner_text = "\n".join(
                            ib.get("text", "") for ib in inner
                            if isinstance(ib, dict) and ib.get("type") == "text"
                        )
                    else:
                        inner_text = str(inner)
                    parts.append(f"<function_results>{inner_text}</function_results>")
        return "\n".join(parts)
    return ""


def _extract_human_text(content) -> str:
    """Extract the human-typed portion of a user message (last plain text block).

    Claude Code injects skills/memory/system-reminders before the human message,
    so the human text is typically the last text block in the content list.
    """
    if isinstance(content, str):
        return content[:800]
    if isinstance(content, list):
        # Walk backwards to find last text block that isn't pure injected context
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text and not text.startswith("<system-reminder>") and not text.startswith("Base directory:"):
                    return text[:800]
    return ""


def _is_human_user_message(content) -> bool:
    """Return True if this user message contains a human-typed exchange (not purely tool results)."""
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list) or not content:
        return False
    return any(
        block.get("type") != "tool_result"
        for block in content
        if isinstance(block, dict)
    )


def _group_exchanges(raw_messages: list[dict]) -> list[dict]:
    """
    Group raw JSONL messages into logical exchanges.

    Each exchange dict has keys:
        human_user_msg      — the opening human user message
        intermediate_pairs  — list of (assistant_msg, tool_result_user_msg) for each tool round-trip
        final_assistant_msg — the closing assistant message (has usage)
        assistant_msgs      — all assistant messages in this exchange (intermediates + final)
        after_compact       — bool

    Assistant messages without usage are skipped (streaming artifacts).
    """
    exchanges = []
    after_compact = False
    pending_human: dict | None = None
    pending_intermediates: list[tuple[dict, dict]] = []
    pending_assistant: dict | None = None
    pending_after_compact: bool = False

    def _flush():
        nonlocal pending_human, pending_intermediates, pending_assistant, pending_after_compact
        if pending_human is not None and pending_assistant is not None:
            all_asst = [asst for asst, _ in pending_intermediates] + [pending_assistant]
            exchanges.append({
                "human_user_msg":     pending_human,
                "intermediate_pairs": list(pending_intermediates),
                "final_assistant_msg": pending_assistant,
                "assistant_msgs":     all_asst,
                "after_compact":      pending_after_compact,
            })
        pending_human = None
        pending_intermediates = []
        pending_assistant = None
        pending_after_compact = False

    for msg in raw_messages:
        msg_type = msg.get("type")

        if msg_type == "system" and msg.get("subtype") == "compact_boundary":
            after_compact = True
            continue

        if msg_type == "user":
            content = msg.get("message", {}).get("content", [])
            if _is_human_user_message(content):
                # New exchange — flush any completed prior exchange first
                _flush()
                pending_human = msg
                pending_intermediates = []
                pending_assistant = None
                pending_after_compact = after_compact
                after_compact = False
            else:
                # Tool-result message: pair with the last seen assistant
                if pending_assistant is not None:
                    pending_intermediates.append((pending_assistant, msg))
                    pending_assistant = None

        elif msg_type == "assistant":
            usage = msg.get("message", {}).get("usage")
            if not usage:
                continue  # skip streaming artifacts
            if pending_human is None:
                continue  # assistant before any human message
            pending_assistant = msg

    # Flush the final open exchange
    _flush()
    return exchanges


def _extract_tool_calls(content) -> tuple[list[str], list[tuple[str, dict]]]:
    """
    Extract tool usage from tool_use blocks in an assistant message.

    Returns:
        files_read: paths from Read/Glob/Grep calls
        tool_calls: list of (tool_name, input_dict) for all tool_use blocks
    """
    if not isinstance(content, list):
        return [], []
    files: list[str] = []
    calls: list[tuple[str, dict]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool = block.get("name", "")
        inp = block.get("input", {}) if isinstance(block.get("input"), dict) else {}
        # Track file reads separately for the files_read field
        if tool == "Read":
            path = inp.get("file_path", "")
            if path:
                files.append(path)
        elif tool == "Glob":
            path = inp.get("pattern", inp.get("path", ""))
            if path:
                files.append(f"Glob:{path}")
        elif tool == "Grep":
            pattern = inp.get("pattern", "")
            path = inp.get("path", "")
            label = f"{pattern!r} in {path}" if path else repr(pattern)
            if label:
                files.append(f"Grep:{label}")
        calls.append((tool, inp))
    return files, calls


def _extract_assistant_text(content) -> str:
    """Extract plain text from an assistant message content."""
    if isinstance(content, str):
        return content[:800]
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)[:800]
    return ""


def load_session(jsonl_file: Path) -> SessionStats:
    """Parse a .jsonl file into a SessionStats."""
    session_id = jsonl_file.stem
    display_name = session_id[:8]

    raw_messages: list[dict] = []
    with open(jsonl_file, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                msg["_lineno"] = lineno
                raw_messages.append(msg)
            except json.JSONDecodeError:
                continue

    grouped = _group_exchanges(raw_messages)
    exchanges: list[ExchangeStats] = []

    for exchange_number, exchange in enumerate(grouped, start=1):
        human_msg = exchange["human_user_msg"]
        final_msg = exchange["final_assistant_msg"]
        intermediate_pairs = exchange["intermediate_pairs"]

        human_content = human_msg.get("message", {}).get("content", [])
        final_content = final_msg.get("message", {}).get("content", [])

        # Sum fresh tokens across all assistant messages in this exchange
        total_input = 0
        total_cache_read = 0
        total_cache_create = 0
        total_cache_create_5m = 0
        total_cache_create_1h = 0
        total_output = 0
        for asst_msg in exchange["assistant_msgs"]:
            usage = asst_msg.get("message", {}).get("usage", {})
            total_input        += usage.get("input_tokens", 0)
            total_cache_read   += usage.get("cache_read_input_tokens", 0)
            total_cache_create += usage.get("cache_creation_input_tokens", 0)
            total_output       += usage.get("output_tokens", 0)
            cc = usage.get("cache_creation", {})
            total_cache_create_5m += cc.get("ephemeral_5m_input_tokens", 0)
            total_cache_create_1h += cc.get("ephemeral_1h_input_tokens", 0)

        fresh_tokens = total_input + total_cache_create

        # Prior assistant content (the message immediately before this exchange's human message)
        # is needed to resolve tool_result → tool_name mappings in the human message.
        # _group_exchanges doesn't carry it, so we find it by walking raw_messages.
        prior_assistant_content: list = []
        human_msg_obj = human_msg.get("message", {})
        for raw in raw_messages:
            if raw is human_msg:
                break
            if raw.get("type") == "assistant" and raw.get("message", {}).get("usage"):
                prior_assistant_content = raw.get("message", {}).get("content", [])

        # Intermediate pairs: strip wrapper dicts to just content lists
        content_pairs = [
            (
                asst.get("message", {}).get("content", []),
                tr.get("message", {}).get("content", []),
            )
            for asst, tr in intermediate_pairs
        ]

        breakdown = categorizer.categorize_exchange(
            human_content=human_content,
            intermediate_pairs=content_pairs,
            prior_assistant_content=prior_assistant_content,
            fresh_tokens=fresh_tokens,
        )

        # Tool calls and files_read come from all assistant messages in the exchange
        files_read: list[str] = []
        tool_calls: list[tuple[str, dict]] = []
        for asst_msg in exchange["assistant_msgs"]:
            asst_content = asst_msg.get("message", {}).get("content", [])
            f, c = _extract_tool_calls(asst_content)
            files_read.extend(f)
            tool_calls.extend(c)

        assistant_text = _extract_assistant_text(final_content)
        human_text = _extract_human_text(human_content)

        model = ""
        for asst_msg in exchange["assistant_msgs"]:
            m = asst_msg.get("message", {}).get("model", "")
            if m and m != "<synthetic>":
                model = m
                break

        line_start = human_msg.get("_lineno", 0)
        line_end = exchange["assistant_msgs"][-1].get("_lineno", 0) if exchange["assistant_msgs"] else line_start

        exchanges.append(ExchangeStats(
            exchange_number=exchange_number,
            timestamp=final_msg.get("timestamp", ""),
            input_tokens=total_input,
            cache_read_tokens=total_cache_read,
            cache_create_tokens=total_cache_create,
            output_tokens=total_output,
            cache_create_5m_tokens=total_cache_create_5m,
            cache_create_1h_tokens=total_cache_create_1h,
            category_breakdown=breakdown,
            after_compact=exchange["after_compact"],
            user_text=human_text,
            assistant_text=assistant_text,
            files_read=files_read,
            tool_calls=tool_calls,
            raw_user=human_msg,
            raw_assistants=exchange["assistant_msgs"],
            jsonl_path=str(jsonl_file),
            jsonl_line_start=line_start,
            jsonl_line_end=line_end,
            model=model,
        ))

    return SessionStats(
        session_id=session_id,
        display_name=display_name,
        exchanges=exchanges,
        jsonl_path=str(jsonl_file),
    )


def apply_session_updates(existing: SessionStats, updated: SessionStats) -> int:
    """Append new exchanges from `updated` to `existing` in-place.

    Returns the number of new exchanges added. Mutates `existing` directly
    so all existing references to the object remain valid.
    """
    old_count = len(existing.exchanges)
    new_count = len(updated.exchanges)
    if new_count <= old_count:
        return 0
    existing.exchanges.extend(updated.exchanges[old_count:])
    return new_count - old_count
