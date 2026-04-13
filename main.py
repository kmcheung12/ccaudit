#!/usr/bin/env python3
# main.py
import argparse
import sys
from pathlib import Path
from parser.loader import list_projects, path_to_slug, PROJECTS_DIR
from tui.app import CCAuditApp


def parse_args():
    parser = argparse.ArgumentParser(description="ccaudit — Claude Code token usage explorer")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-a", "--all", action="store_true",
                       help="Read all Claude projects (default)")
    group.add_argument("-d", "--dir", metavar="PATH",
                       help="Limit to a single project directory")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.dir:
        target = Path(args.dir).expanduser().resolve()
        slug = path_to_slug(target)
        all_projects = list_projects()
        projects = [p for p in all_projects if p.project_slug == slug]
        if not projects:
            print(f"error: no Claude project found for {str(target)!r} (slug: {slug!r})", file=sys.stderr)
            sys.exit(1)
    else:
        projects = list_projects()

    app = CCAuditApp(projects=projects)
    app.run()
