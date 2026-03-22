from __future__ import annotations
import json
from pathlib import Path
from parser.models import ProjectStats, SessionStats, TurnStats, CategoryBreakdown
from parser import categorizer

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def slug_to_display(slug: str) -> str:
    """Convert -Users-alan-code-my-project to my-project."""
    parts = slug.lstrip("-").split("-")
    if len(parts) >= 4:
        return "-".join(parts[3:])
    return slug


def list_projects(projects_dir: Path = PROJECTS_DIR) -> list[ProjectStats]:
    """Return unloaded ProjectStats for each subdirectory."""
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


def load_project(project: ProjectStats, projects_dir: Path = PROJECTS_DIR) -> None:
    """Load all sessions for a project in-place. Sets project.loaded = True."""
    try:
        project_dir = projects_dir / project.project_slug
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
                    if isinstance(inner, list):
                        for ib in inner:
                            if isinstance(ib, dict) and ib.get("type") == "text":
                                parts.append(ib.get("text", ""))
                    else:
                        parts.append(str(inner))
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


def _extract_files_read(content) -> list[str]:
    """Extract file paths from tool_use blocks in an assistant message."""
    if not isinstance(content, list):
        return []
    files = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool = block.get("name", "")
        inp = block.get("input", {})
        if tool == "Read":
            path = inp.get("file_path", "")
            if path:
                files.append(path)
        elif tool in ("Glob", "Grep"):
            path = inp.get("path") or inp.get("pattern") or ""
            if path:
                files.append(f"{tool}:{path}")
    return files


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
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw_messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Determine positions after compact boundaries
    compact_positions: set[int] = set()
    in_compact = False
    for i, msg in enumerate(raw_messages):
        if msg.get("type") == "system" and msg.get("subtype") == "compact_boundary":
            in_compact = True
        if in_compact and msg.get("type") == "user":
            compact_positions.add(i)
            in_compact = False

    # Determine system prompt baseline from first assistant message
    system_prompt_tokens = 0
    for msg in raw_messages:
        if msg.get("type") == "assistant":
            usage = msg.get("message", {}).get("usage", {})
            if usage:
                system_prompt_tokens = usage.get("cache_read_input_tokens", 0)
                break

    # Build turns: pair each user message with the next assistant message
    turns: list[TurnStats] = []
    pending_user_text: str | None = None
    pending_human_text: str = ""
    after_compact = False
    turn_number = 0
    is_first_turn = True  # system prompt only attributed to the first turn

    for i, msg in enumerate(raw_messages):
        msg_type = msg.get("type")

        if msg_type == "user":
            content = msg.get("message", {}).get("content", "")
            pending_user_text = _extract_text(content)
            pending_human_text = _extract_human_text(content)
            after_compact = i in compact_positions

        elif msg_type == "assistant":
            usage = msg.get("message", {}).get("usage")
            if not usage:
                continue

            input_tokens = usage.get("input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)

            # System prompt only attributed to the first turn of the session
            sp_tokens = system_prompt_tokens if is_first_turn else 0

            text = pending_user_text or ""
            breakdown = categorizer.categorize(
                text=text,
                input_tokens=input_tokens,
                system_prompt_tokens=sp_tokens,
            )

            assistant_content = msg.get("message", {}).get("content", "")
            assistant_text = _extract_assistant_text(assistant_content)
            files_read = _extract_files_read(assistant_content)

            turn_number += 1
            turns.append(TurnStats(
                turn_number=turn_number,
                timestamp=msg.get("timestamp", ""),
                input_tokens=input_tokens,
                cache_read_tokens=cache_read,
                cache_create_tokens=cache_create,
                output_tokens=output_tokens,
                category_breakdown=breakdown,
                after_compact=after_compact,
                user_text=pending_human_text,
                assistant_text=assistant_text,
                files_read=files_read,
            ))
            pending_user_text = None
            pending_human_text = ""
            after_compact = False
            is_first_turn = False

    first_timestamp = None
    for msg in raw_messages:
        if "timestamp" in msg:
            first_timestamp = msg["timestamp"]
            break

    return SessionStats(
        session_id=session_id,
        display_name=display_name,
        first_timestamp=first_timestamp,
        turns=turns,
    )
