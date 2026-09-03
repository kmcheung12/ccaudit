import functools
import json
import runpy
import sys
import pytest
from pathlib import Path
import parser.loader
from parser.loader import path_to_slug
from tui.app import CCAuditApp
from main import parse_args, resolve_projects

MAIN_PY = str(Path(__file__).resolve().parent.parent / "main.py")


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


def test_parse_args_rejects_removed_all_flag():
    """-a/--all was removed; reading every project is simply the absence of -d."""
    with pytest.raises(SystemExit):
        parse_args(["-a"])
    with pytest.raises(SystemExit):
        parse_args(["--all"])


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


# No --dir means every project, and -s still applies.

def test_no_flags_reads_every_project(dirs):
    args = parse_args([])
    assert args.dir is None
    projects, error = resolve_projects(args.source, args.dir, **dirs)
    assert error is None
    assert len(projects) == 3


def test_source_still_narrows_when_no_dir_is_given(dirs):
    """Dropping -a must not make -s a no-op on the read-everything path."""
    args = parse_args(["-s", "codex"])
    projects, error = resolve_projects(args.source, args.dir, **dirs)
    assert error is None
    assert sorted(p.project_slug for p in projects) == ["-Users-alan-code-both",
                                                        "-Users-alan-code-codexonly"]


# --dir path forms


@pytest.fixture
def real_dir_project(dirs, tmp_path):
    """A Claude project whose slug corresponds to a directory that exists on disk.

    Needed because --dir resolves the path against the real filesystem, so the
    fixture's fake /Users/alan paths can't be reached via '.' or '~'.
    """
    home = (tmp_path / "home").resolve()
    target = home / "code" / "realproj"
    target.mkdir(parents=True)
    slug = path_to_slug(target)
    (dirs["projects_dir"] / slug).mkdir()
    return {"home": home, "target": target, "slug": slug}


def test_dir_accepts_absolute_path(dirs, real_dir_project):
    projects, error = resolve_projects("all", str(real_dir_project["target"]), **dirs)
    assert error is None
    assert [p.project_slug for p in projects] == [real_dir_project["slug"]]


def test_dir_accepts_relative_dot_path(dirs, real_dir_project, monkeypatch):
    monkeypatch.chdir(real_dir_project["target"])
    projects, error = resolve_projects("all", ".", **dirs)
    assert error is None
    assert [p.project_slug for p in projects] == [real_dir_project["slug"]]


def test_dir_accepts_tilde_prefixed_path(dirs, real_dir_project, monkeypatch):
    monkeypatch.setenv("HOME", str(real_dir_project["home"]))
    projects, error = resolve_projects("all", "~/code/realproj", **dirs)
    assert error is None
    assert [p.project_slug for p in projects] == [real_dir_project["slug"]]


def test_dir_accepts_trailing_slash(dirs, real_dir_project):
    projects, error = resolve_projects("all", str(real_dir_project["target"]) + "/", **dirs)
    assert error is None
    assert [p.project_slug for p in projects] == [real_dir_project["slug"]]


# __main__ wiring — drive the real module end to end


@pytest.fixture
def run_main(monkeypatch, capsys, dirs):
    """Execute main.py as __main__ with isolated discovery dirs and a stubbed App.run.

    list_projects' directory arguments are *default parameter values*, bound when
    parser.loader was defined, so patching PROJECTS_DIR / CODEX_SESSIONS_DIR has
    no effect. Binding the fixture dirs onto the function itself does.
    """
    calls = []

    def fake_run(self, *args, **kwargs):
        calls.append(self._global.projects)

    monkeypatch.setattr(CCAuditApp, "run", fake_run)
    monkeypatch.setattr(
        parser.loader, "list_projects",
        functools.partial(parser.loader.list_projects, **dirs),
    )

    def _run(argv):
        monkeypatch.setattr(sys, "argv", ["main.py", *argv])
        calls.clear()
        capsys.readouterr()
        code = 0
        try:
            runpy.run_path(MAIN_PY, run_name="__main__")
        except SystemExit as exc:
            code = exc.code if exc.code is not None else 0
        captured = capsys.readouterr()
        return code, captured, calls

    return _run


def test_main_runs_the_app_with_the_resolved_projects(run_main):
    code, captured, calls = run_main([])
    assert code == 0
    assert captured.err == ""
    assert len(calls) == 1
    # Only the fixture dirs could produce exactly these three slugs.
    assert sorted(p.project_slug for p in calls[0]) == ["-Users-alan-code-both",
                                                        "-Users-alan-code-claudeonly",
                                                        "-Users-alan-code-codexonly"]


@pytest.mark.parametrize("source,expected", [
    ("all", ["-Users-alan-code-both", "-Users-alan-code-claudeonly",
             "-Users-alan-code-codexonly"]),
    ("claude", ["-Users-alan-code-both", "-Users-alan-code-claudeonly"]),
    ("codex", ["-Users-alan-code-both", "-Users-alan-code-codexonly"]),
])
def test_main_honours_every_source_value(run_main, source, expected):
    code, captured, calls = run_main(["-s", source])
    assert code == 0
    assert captured.err == ""
    assert sorted(p.project_slug for p in calls[0]) == expected


def test_main_narrows_to_a_single_project_with_dir(run_main):
    code, captured, calls = run_main(["-d", "/Users/alan/code/both"])
    assert code == 0
    assert [p.project_slug for p in calls[0]] == ["-Users-alan-code-both"]


def test_main_exits_1_and_prints_error_to_stderr_when_dir_matches_nothing(run_main):
    code, captured, calls = run_main(["-d", "/Users/alan/code/nope"])
    assert code == 1
    assert calls == []          # the app is never constructed or run
    assert captured.out == ""
    assert "no project found" in captured.err
    assert "-Users-alan-code-nope" in captured.err


def test_main_exits_2_on_bad_arguments(run_main):
    code, captured, calls = run_main(["-s", "bogus"])
    assert code == 2
    assert calls == []
    assert "invalid choice" in captured.err
