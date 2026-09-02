#!/usr/bin/env python3
# main.py
import argparse
import sys
from pathlib import Path
from parser.loader import list_projects, path_to_slug, PROJECTS_DIR
from tui.app import CCAuditApp


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="ccaudit — coding agent token usage explorer")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-a", "--all", action="store_true",
                       help="Read all projects (default)")
    group.add_argument("-d", "--dir", metavar="PATH",
                       help="Limit to a single project directory")
    parser.add_argument("-s", "--source", choices=("claude", "codex", "all"), default="all",
                        help="Which harness logs to read (default: all)")
    return parser.parse_args(argv)


def resolve_projects(source: str, dir_arg: str | None, **discovery_kwargs):
    """Discover projects for the given source, optionally narrowed to one directory.

    Returns (projects, error_message). error_message is None on success, or a
    string to print when --dir matches no discovered project.
    """
    all_projects = list_projects(
        include_claude=source in ("claude", "all"),
        include_codex=source in ("codex", "all"),
        **discovery_kwargs,
    )
    if not dir_arg:
        return all_projects, None

    target = Path(dir_arg).expanduser().resolve()
    slug = path_to_slug(target)
    projects = [p for p in all_projects if p.project_slug == slug]
    if not projects:
        return [], (f"error: no project found for {str(target)!r} "
                    f"in {source} logs (slug: {slug!r})")
    return projects, None


if __name__ == "__main__":
    args = parse_args()
    projects, error = resolve_projects(args.source, args.dir)
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)

    app = CCAuditApp(projects=projects)
    app.run()
