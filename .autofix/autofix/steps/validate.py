"""Step 4 (deterministic): our own guard check + test runs. The fix model's word counts for nothing."""
from __future__ import annotations

from ..config import Config
from ..guards import check_diff
from ..proc import git
from ..runlog import RunLog, scrub
from ..state import Attempt, RunState
from .collect import run_tests


def current_diff(cfg: Config) -> str:
    return git(cfg.repo, "diff", "HEAD")


def reset_worktree(cfg: Config) -> None:
    # Safe because the pipeline refuses to start with uncommitted tracked changes.
    git(cfg.repo, "reset", "-q", "--hard", "HEAD")


def validate(cfg: Config, state: RunState, attempt: Attempt, log: RunLog) -> None:
    diff = current_diff(cfg)
    attempt.diff = scrub(diff)
    category = state.classification.category if state.classification else None
    attempt.guard_violations = check_diff(diff, category, cfg.max_diff_lines)
    log.event("validate", "guards", violations=attempt.guard_violations)
    if attempt.guard_violations or attempt.error:
        return
    failing = state.initial.failing_tests if state.initial else []
    attempt.targeted = run_tests(cfg, failing) if failing else run_tests(cfg)
    log.event("validate", "targeted", exit_code=attempt.targeted.exit_code)
    if not attempt.targeted.passed:
        return
    attempt.full = run_tests(cfg) if failing else attempt.targeted
    log.event("validate", "full", exit_code=attempt.full.exit_code, failing=attempt.full.failing_tests)
