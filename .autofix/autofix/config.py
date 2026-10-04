"""All tunables come from env vars so the GitHub workflow can override them without code changes."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _current_branch(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                         capture_output=True, text=True)
    return out.stdout.strip() or "main"


@dataclass(frozen=True)
class Config:
    repo: Path
    dry_run: bool = False
    classify_model: str = "claude-sonnet-5-5"
    fix_model: str = "claude-opus-5-5"
    classify_max_turns: int = 15
    fix_max_turns: int = 30
    max_fix_attempts: int = 3
    min_confidence: float = 0.6
    max_diff_lines: int = 200
    timeout_s: int = 20 * 60
    test_timeout_s: int = 10 * 60
    # Must accept test ids as trailing args (pytest does) so validate can re-run just the failures.
    test_command: str = "python -m pytest -q"
    base_branch: str = "main"
    # The branch CI ran on; used for the "never run on autofix/*" loop guard.
    head_branch: str = ""
    skills: tuple[str, ...] = ("general", "python-pytest")
    skills_dir: Path = DEFAULT_SKILLS_DIR
    runs_dir: Path = field(default_factory=lambda: Path("runs"))

    @classmethod
    def from_env(cls, repo: Path, dry_run: bool = False) -> "Config":
        repo = repo.resolve()
        env = os.environ.get
        base = env("BASE_BRANCH") or _current_branch(repo)
        return cls(
            repo=repo,
            dry_run=dry_run or env("AUTOFIX_DRY_RUN", "") == "1",
            classify_model=env("CLASSIFY_MODEL", cls.classify_model),
            fix_model=env("FIX_MODEL", cls.fix_model),
            classify_max_turns=_int("CLASSIFY_MAX_TURNS", cls.classify_max_turns),
            fix_max_turns=_int("FIX_MAX_TURNS", cls.fix_max_turns),
            max_fix_attempts=_int("MAX_FIX_ATTEMPTS", cls.max_fix_attempts),
            min_confidence=float(env("MIN_CONFIDENCE", cls.min_confidence)),
            max_diff_lines=_int("MAX_DIFF_LINES", cls.max_diff_lines),
            timeout_s=_int("AUTOFIX_TIMEOUT_S", cls.timeout_s),
            test_command=env("TEST_COMMAND", cls.test_command),
            base_branch=base,
            head_branch=env("HEAD_BRANCH") or base,
            skills=tuple(s.strip() for s in env("SKILLS", ",".join(cls.skills)).split(",") if s.strip()),
            skills_dir=Path(env("SKILLS_DIR", str(DEFAULT_SKILLS_DIR))),
            runs_dir=Path(env("RUNS_DIR", "runs")).resolve(),
        )


def auth_mode() -> str:
    """Which credential the Claude CLI will use. Never returns the value itself."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY"
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "CLAUDE_CODE_OAUTH_TOKEN"
    return "claude-cli-login"
