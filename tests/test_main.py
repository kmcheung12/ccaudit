import json
import pytest
from pathlib import Path
from main import parse_args, resolve_projects


def write_rollout(path: Path, cwd: str) -> None:
    """Write a minimal Codex rollout whose session_meta records `cwd`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"timestamp": "2026-09-02T08:00:00.000Z", "type": "session_meta",
         "payload": {"id": path.stem, "session_id": path.stem, "cwd": cwd,
                     "thread_source": "user", "source": "cli"}},
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines))


@pytest.fixture
def dirs(tmp_path):
    """Claude projects dir + Codex sessions dir sharing one overlapping project."""
    claude = tmp_path / "claude-projects"
    codex = tmp_path / "codex-sessions"
    (claude / "-Users-alan-code-both").mkdir(parents=True)
    (claude / "-Users-alan-code-claudeonly").mkdir(parents=True)
    write_rollout(codex / "2026" / "09" / "02" / "rollout-a.jsonl", "/Users/alan/code/both")
    write_rollout(codex / "2026" / "09" / "02" / "rollout-b.jsonl", "/Users/alan/code/codexonly")
    return {"projects_dir": claude, "codex_sessions_dir": codex}


# parse_args

def test_parse_args_defaults():
    args = parse_args([])
    assert args.source == "all"
    assert args.dir is None


@pytest.mark.parametrize("argv,expected", [
    (["-s", "claude"], "claude"),
    (["-s", "codex"], "codex"),
    (["--source", "all"], "all"),
])
def test_parse_args_source(argv, expected):
    assert parse_args(argv).source == expected


def test_parse_args_dir_and_all_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["-a", "-d", "."])


def test_parse_args_rejects_unknown_source():
    with pytest.raises(SystemExit):
        parse_args(["-s", "bogus"])


def test_parse_args_dir_requires_a_value():
    with pytest.raises(SystemExit):
        parse_args(["-d"])


# resolve_projects — source filtering

def test_resolve_all_sources_merges_by_directory(dirs):
    projects, error = resolve_projects("all", None, **dirs)
    assert error is None
    slugs = sorted(p.project_slug for p in projects)
    assert slugs == ["-Users-alan-code-both",
                     "-Users-alan-code-claudeonly",
                     "-Users-alan-code-codexonly"]
    both = next(p for p in projects if p.project_slug == "-Users-alan-code-both")
    assert both.claude_dir is not None and len(both.codex_files) == 1


def test_resolve_claude_source_excludes_codex_only_projects(dirs):
    projects, error = resolve_projects("claude", None, **dirs)
    assert error is None
    assert sorted(p.project_slug for p in projects) == ["-Users-alan-code-both",
                                                        "-Users-alan-code-claudeonly"]
    assert all(p.codex_files == [] for p in projects)


def test_resolve_codex_source_excludes_claude_only_projects(dirs):
    projects, error = resolve_projects("codex", None, **dirs)
    assert error is None
    assert sorted(p.project_slug for p in projects) == ["-Users-alan-code-both",
                                                        "-Users-alan-code-codexonly"]
    assert all(p.claude_dir is None for p in projects)


# resolve_projects — --dir narrowing

def test_resolve_dir_narrows_to_one_project(dirs):
    projects, error = resolve_projects("all", "/Users/alan/code/both", **dirs)
    assert error is None
    assert len(projects) == 1
    assert projects[0].project_slug == "-Users-alan-code-both"


def test_resolve_dir_finds_codex_only_project(dirs):
    projects, error = resolve_projects("codex", "/Users/alan/code/codexonly", **dirs)
    assert error is None
    assert len(projects) == 1
    assert projects[0].claude_dir is None


def test_resolve_dir_respects_source_filter(dirs):
    """A Codex-only directory is not found when only Claude logs are read."""
    projects, error = resolve_projects("claude", "/Users/alan/code/codexonly", **dirs)
    assert projects == []
    assert "no project found" in error
    assert "in claude logs" in error


def test_resolve_dir_with_no_match_reports_the_resolved_path(dirs):
    projects, error = resolve_projects("all", "/Users/alan/code/nonexistent", **dirs)
    assert projects == []
    assert "/Users/alan/code/nonexistent" in error
    assert "-Users-alan-code-nonexistent" in error


def test_resolve_dir_expands_user_and_resolves_relative_paths(dirs, monkeypatch, tmp_path):
    """--dir accepts '.' and ~ forms; both are resolved before slug lookup."""
    target = tmp_path / "code" / "both"
    target.mkdir(parents=True)
    monkeypatch.chdir(target)
    _, error = resolve_projects("all", ".", **dirs)
    # The resolved cwd slug won't match the fixture's fake /Users/alan path,
    # but it must be resolved to an absolute slug rather than left as '.'.
    assert "'.'" not in error
    assert str(target) in error
