"""CLI: python -m autofix run [--dry-run] [--repo PATH]"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import pipeline
from .config import Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofix")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run the pipeline against a repo")
    p_run.add_argument("--repo", type=Path, default=Path.cwd())
    p_run.add_argument("--dry-run", action="store_true", help="validate a fix but do not push or open a PR")
    args = parser.parse_args(argv)

    # In Actions an unset secret arrives as "", which would shadow the other auth method.
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        if os.environ.get(name) == "":
            del os.environ[name]

    cfg = Config.from_env(args.repo, dry_run=args.dry_run)
    state = pipeline.run(cfg)
    # "reported" is a correct outcome, not an error; only a pipeline crash fails the job.
    return 1 if state.reason.startswith("pipeline error") else 0


if __name__ == "__main__":
    sys.exit(main())
