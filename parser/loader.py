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
    after_compact = False
    turn_number = 0

    for i, msg in enumerate(raw_messages):
        msg_type = msg.get("type")

        if msg_type == "user":
            content = msg.get("message", {}).get("content", "")
            pending_user_text = _extract_text(content)
            after_compact = i in compact_positions

        elif msg_type == "assistant":
            usage = msg.get("message", {}).get("usage")
            if not usage:
                continue

            input_tokens = usage.get("input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)

            text = pending_user_text or ""
            breakdown = categorizer.categorize(
                text=text,
                input_tokens=input_tokens,
                system_prompt_tokens=system_prompt_tokens,
            )

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
            ))
            pending_user_text = None
            after_compact = False

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
