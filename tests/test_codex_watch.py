# tests/test_codex_watch.py
"""Live reload for Codex rollouts.

Codex stores rollouts by date, not by project, so one day directory holds files
for many working directories. These tests cover routing a new rollout to the
right project, day/month rollover, and the kqueue plumbing underneath.
"""
import asyncio
import json
import threading
import time
from pathlib import Path

from parser.loader import list_projects, load_project
from tui.app import CCAuditApp
from tui.watcher import FileWatcher

ALPHA = "/Users/alan/code/alpha"
BETA = "/Users/alan/code/beta"
ALPHA_SLUG = "-Users-alan-code-alpha"
BETA_SLUG = "-Users-alan-code-beta"


def _usage(inp: int, out: int) -> dict:
    return {
        "input_tokens": inp,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": out,
        "reasoning_output_tokens": 0,
        "total_tokens": inp + out,
    }


def _rollout_lines(cwd: str, session_id: str, exchanges: int = 1) -> list[dict]:
    """A minimal but realistic rollout: session_meta, turn_context, then N exchanges."""
    lines = [
        {"timestamp": "2026-09-02T00:00:00.000Z", "type": "session_meta",
         "payload": {"id": session_id, "session_id": session_id, "cwd": cwd}},
        {"timestamp": "2026-09-02T00:00:01.000Z", "type": "turn_context",
         "payload": {"model": "gpt-5.6-sol", "cwd": cwd}},
    ]
    for n in range(1, exchanges + 1):
        lines.append({"timestamp": f"2026-09-02T00:0{n}:02.000Z", "type": "event_msg",
                      "payload": {"type": "user_message", "message": f"question {n}"}})
        lines.append({"timestamp": f"2026-09-02T00:0{n}:03.000Z", "type": "event_msg",
                      "payload": {"type": "agent_message", "message": f"answer {n}"}})
        lines.append({"timestamp": f"2026-09-02T00:0{n}:04.000Z", "type": "event_msg",
                      "payload": {"type": "token_count",
                                  "info": {"total_token_usage": _usage(1000 * n, 100 * n)}}})
    return lines


def _write_rollout(path: Path, cwd: str, session_id: str, exchanges: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(l) for l in _rollout_lines(cwd, session_id, exchanges)))
    return path


def _day_dir(root: Path, year="2026", month="09", day="02") -> Path:
    return root / year / month / day


def _make_projects(root: Path, missing_claude_dir: Path):
    """Discover the fake Codex tree and eagerly load every project."""
    projects = list_projects(
        projects_dir=missing_claude_dir,
        codex_sessions_dir=root,
    )
    for project in projects:
        load_project(project)
    return projects


def _by_slug(projects, slug):
    return next(p for p in projects if p.project_slug == slug)


def _drive(app: CCAuditApp, action) -> None:
    """Mount `app` for real, run `action(app)` on the Textual thread, then exit."""
    async def _run() -> None:
        async with app.run_test() as pilot:
            action(app)
            await pilot.pause()
    asyncio.run(_run())


def _fixture(tmp_path):
    """A sessions tree whose single day directory holds a rollout for two cwds."""
    root = tmp_path / "sessions"
    _write_rollout(_day_dir(root) / "rollout-2026-09-02T00-00-00-aaaa.jsonl", ALPHA, "aaaa1111")
    _write_rollout(_day_dir(root) / "rollout-2026-09-02T01-00-00-bbbb.jsonl", BETA, "bbbb2222")
    projects = _make_projects(root, tmp_path / "no-claude-projects")
    return root, projects


# ---------------------------------------------------------------- routing

def test_new_rollout_routes_to_its_own_project(tmp_path):
    root, projects = _fixture(tmp_path)
    alpha, beta = _by_slug(projects, ALPHA_SLUG), _by_slug(projects, BETA_SLUG)
    assert (len(alpha.sessions), len(beta.sessions)) == (1, 1)

    new_file = _write_rollout(
        _day_dir(root) / "rollout-2026-09-02T02-00-00-cccc.jsonl", ALPHA, "cccc3333"
    )
    app = CCAuditApp(projects=projects)
    _drive(app, lambda a: a._on_dir_changed(str(_day_dir(root))))

    assert len(alpha.sessions) == 2, "new rollout should land under its own project"
    assert len(beta.sessions) == 1, "the other project must be left alone"
    assert str(new_file) in alpha.codex_files
    assert alpha.sessions[-1].session_id == "cccc3333"


def test_rollout_for_unknown_directory_is_ignored(tmp_path):
    root, projects = _fixture(tmp_path)
    _write_rollout(
        _day_dir(root) / "rollout-2026-09-02T02-00-00-dddd.jsonl",
        "/Users/alan/code/never-seen", "dddd4444",
    )
    app = CCAuditApp(projects=projects)
    _drive(app, lambda a: a._on_dir_changed(str(_day_dir(root))))

    assert all(len(p.sessions) == 1 for p in projects)


def test_unloaded_project_records_the_file_without_parsing_it(tmp_path):
    root, projects = _fixture(tmp_path)
    alpha = _by_slug(projects, ALPHA_SLUG)
    alpha.loaded = False
    new_file = _write_rollout(
        _day_dir(root) / "rollout-2026-09-02T02-00-00-eeee.jsonl", ALPHA, "eeee5555"
    )
    app = CCAuditApp(projects=projects)
    _drive(app, lambda a: a._on_dir_changed(str(_day_dir(root))))

    assert str(new_file) in alpha.codex_files
    assert len(alpha.sessions) == 1  # load_project() will pick it up on expand


def test_claude_directory_still_routes_by_directory(tmp_path):
    """The Codex changes must not disturb the one-directory-per-project path."""
    root = tmp_path / "sessions"
    _write_rollout(_day_dir(root) / "rollout-2026-09-02T00-00-00-aaaa.jsonl", ALPHA, "aaaa1111")
    claude_dir = tmp_path / "projects" / ALPHA_SLUG
    claude_dir.mkdir(parents=True)
    projects = list_projects(projects_dir=tmp_path / "projects", codex_sessions_dir=root)
    for project in projects:
        load_project(project)
    alpha = _by_slug(projects, ALPHA_SLUG)
    assert len(alpha.sessions) == 1

    (claude_dir / "session1.jsonl").write_text("\n".join(json.dumps(m) for m in [
        {"type": "user", "message": {"role": "user", "content": "hi"},
         "timestamp": "2026-09-02T00:00:00Z", "uuid": "u1"},
        {"type": "assistant", "timestamp": "2026-09-02T00:00:01Z", "uuid": "a1",
         "message": {"role": "assistant", "content": "yo", "model": "claude-sonnet-4",
                     "usage": {"input_tokens": 10, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0, "output_tokens": 5}}},
    ]))
    app = CCAuditApp(projects=projects)
    _drive(app, lambda a: a._on_dir_changed(str(claude_dir)))

    assert len(alpha.sessions) == 2
    assert alpha.sessions[-1].total_input_tokens == 10


# ---------------------------------------------------------------- rollover

def test_new_day_directory_is_picked_up(tmp_path):
    root, projects = _fixture(tmp_path)
    beta = _by_slug(projects, BETA_SLUG)
    month_dir = root / "2026" / "09"

    app = CCAuditApp(projects=projects)

    def _rollover(a: CCAuditApp) -> None:
        assert str(_day_dir(root)) in a._codex_day_dirs
        _write_rollout(
            _day_dir(root, day="03") / "rollout-2026-09-03T00-00-00-ffff.jsonl",
            BETA, "ffff6666",
        )
        a._on_dir_changed(str(month_dir))  # creating a day dir is a write to its month
        assert str(_day_dir(root, day="03")) in a._codex_day_dirs

    _drive(app, _rollover)

    assert len(beta.sessions) == 2
    assert beta.sessions[-1].session_id == "ffff6666"
    assert len(_by_slug(projects, ALPHA_SLUG).sessions) == 1


def test_new_month_directory_is_picked_up(tmp_path):
    root, projects = _fixture(tmp_path)
    alpha = _by_slug(projects, ALPHA_SLUG)
    year_dir = root / "2026"

    app = CCAuditApp(projects=projects)

    def _rollover(a: CCAuditApp) -> None:
        _write_rollout(
            _day_dir(root, month="10", day="01") / "rollout-2026-10-01T00-00-00-9999.jsonl",
            ALPHA, "99990000",
        )
        a._on_dir_changed(str(year_dir))  # a new month is a write to the year dir

    _drive(app, _rollover)

    assert len(alpha.sessions) == 2
    assert alpha.sessions[-1].session_id == "99990000"


def test_only_the_newest_day_directories_are_watched(tmp_path):
    """Old day directories can never gain files, so they must not hold fds."""
    root = tmp_path / "sessions"
    for day in ("01", "02", "03", "04"):
        _write_rollout(
            _day_dir(root, day=day) / f"rollout-2026-09-{day}T00-00-00-aaaa.jsonl",
            ALPHA, f"aaaa{day}",
        )
    projects = _make_projects(root, tmp_path / "no-claude-projects")
    app = CCAuditApp(projects=projects)
    seen: list[set] = []
    _drive(app, lambda a: seen.append(set(a._codex_day_dirs)))

    assert seen[0] == {str(_day_dir(root, day="03")), str(_day_dir(root, day="04"))}


# ---------------------------------------------------------------- mid-write files

def test_rollout_created_before_its_first_line_is_retried(tmp_path):
    root, projects = _fixture(tmp_path)
    alpha = _by_slug(projects, ALPHA_SLUG)
    half_written = _day_dir(root) / "rollout-2026-09-02T03-00-00-7777.jsonl"
    half_written.write_text("")  # created, nothing flushed yet

    app = CCAuditApp(projects=projects)

    def _appear_then_flush(a: CCAuditApp) -> None:
        a._on_dir_changed(str(_day_dir(root)))
        assert str(half_written) in a._codex_pending
        assert len(alpha.sessions) == 1
        _write_rollout(half_written, ALPHA, "77778888")
        a._on_jsonl_changed(str(half_written))
        assert str(half_written) not in a._codex_pending

    _drive(app, _appear_then_flush)

    assert len(alpha.sessions) == 2
    assert alpha.sessions[-1].session_id == "77778888"


def test_malformed_rollout_never_raises(tmp_path):
    root, projects = _fixture(tmp_path)
    bad = _day_dir(root) / "rollout-2026-09-02T04-00-00-6666.jsonl"
    bad.write_text("{not json at all\n\x00\x00")

    app = CCAuditApp(projects=projects)
    _drive(app, lambda a: a._on_dir_changed(str(_day_dir(root))))

    assert all(len(p.sessions) == 1 for p in projects)


# ---------------------------------------------------------------- loader dispatch

def test_growing_codex_rollout_is_reparsed_with_the_codex_loader(tmp_path):
    root, projects = _fixture(tmp_path)
    alpha = _by_slug(projects, ALPHA_SLUG)
    session = alpha.sessions[0]
    assert len(session.exchanges) == 1

    rollout = Path(session.jsonl_path)
    _write_rollout(rollout, ALPHA, "aaaa1111", exchanges=2)

    app = CCAuditApp(projects=projects)
    _drive(app, lambda a: a._on_jsonl_changed(str(rollout)))

    # The Claude parser yields zero exchanges for a rollout, so a non-empty
    # result proves the Codex loader was chosen.
    assert len(session.exchanges) == 2
    assert session.exchanges[1].output_tokens == 100


def test_growing_trailing_exchange_is_refreshed_in_place(tmp_path):
    root, projects = _fixture(tmp_path)
    alpha = _by_slug(projects, ALPHA_SLUG)
    session = alpha.sessions[0]
    exchange = session.exchanges[0]
    assert exchange.output_tokens == 100

    rollout = Path(session.jsonl_path)
    lines = _rollout_lines(ALPHA, "aaaa1111")
    lines.append({"timestamp": "2026-09-02T00:01:05.000Z", "type": "event_msg",
                  "payload": {"type": "token_count",
                              "info": {"total_token_usage": _usage(1500, 400)}}})
    rollout.write_text("\n".join(json.dumps(l) for l in lines))

    app = CCAuditApp(projects=projects)
    _drive(app, lambda a: a._on_jsonl_changed(str(rollout)))

    assert len(session.exchanges) == 1
    assert session.exchanges[0] is exchange, "must mutate, not replace (tree holds the object)"
    assert exchange.output_tokens == 400
    assert exchange.input_tokens == 1500


# ---------------------------------------------------------------- real kqueue

def test_filewatcher_sees_codex_shaped_tree(tmp_path):
    """Smoke test against real kqueue on a YYYY/MM/DD tree."""
    root = tmp_path / "sessions"
    day = _day_dir(root)
    rollout = _write_rollout(day / "rollout-2026-09-02T00-00-00-aaaa.jsonl", ALPHA, "aaaa1111")

    files: list[str] = []
    dirs: list[str] = []
    fired = threading.Event()

    def _on_file(path: str) -> None:
        files.append(path)
        fired.set()

    def _on_dir(path: str) -> None:
        dirs.append(path)
        fired.set()

    watcher = FileWatcher(on_file_changed=_on_file, on_dir_changed=_on_dir)
    watcher.watch_dir(str(root / "2026" / "09"))
    watcher.watch_dir(str(day))
    watcher.watch_file(str(rollout))
    watcher.start()
    try:
        time.sleep(0.6)  # let the command queue drain into kqueue

        with open(rollout, "a") as f:
            f.write(json.dumps({"type": "event_msg", "payload": {"type": "agent_message",
                                                                 "message": "more"}}) + "\n")
            f.flush()
        assert fired.wait(5), "append to a rollout should fire on_file_changed"
        assert str(rollout) in files

        fired.clear()
        _write_rollout(day / "rollout-2026-09-02T05-00-00-5555.jsonl", BETA, "55556666")
        assert fired.wait(5), "a new rollout should fire on_dir_changed for its day dir"
        assert str(day) in dirs

        fired.clear()
        (root / "2026" / "09" / "03").mkdir()
        assert fired.wait(5), "a new day dir should fire on_dir_changed for its month dir"
        assert str(root / "2026" / "09") in dirs
    finally:
        watcher.stop()
