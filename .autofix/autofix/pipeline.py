"""The fixed pipeline. Code decides the order of steps; the LLM only works inside classify and fix.

collect -> classify -> (fix -> validate) x N -> open_pr
                 \\-> report (flaky / infra / low confidence / all attempts failed)
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from .config import Config, auth_mode
from .llm import LLM, ClaudeAgentLLM
from .proc import git
from .runlog import RunLog
from .state import Category, Outcome, RunState
from .steps.classify import classify
from .steps.collect import collect
from .steps.fix import fix
from .steps.open_pr import open_pr
from .steps.report import report
from .steps.validate import reset_worktree, validate


class Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return max(0.0, self.end - time.monotonic())


def run(cfg: Config, llm: LLM | None = None) -> RunState:
    llm = llm or ClaudeAgentLLM()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    state = RunState(run_id=run_id, repo=str(cfg.repo), base_branch=cfg.base_branch)
    log = RunLog(cfg.runs_dir / f"{run_id}.jsonl")
    log.event("start", "config", repo=str(cfg.repo), dry_run=cfg.dry_run, base=cfg.base_branch,
              head=cfg.head_branch, auth=auth_mode(), classify_model=cfg.classify_model, fix_model=cfg.fix_model)
    try:
        _run_steps(cfg, state, llm, log)
    except Exception as e:  # any crash still ends in a report, never a half-pushed branch
        state.outcome, state.reason = Outcome.reported, f"pipeline error: {type(e).__name__}: {e}"
        log.event("pipeline", "error", error=state.reason)
    report(cfg, state, log)
    log.event("end", "outcome", outcome=state.outcome, reason=state.reason, cost_usd=state.cost_usd)
    return state


def _stop(state: RunState, outcome: Outcome, reason: str) -> None:
    state.outcome, state.reason = outcome, reason


def _run_steps(cfg: Config, state: RunState, llm: LLM, log: RunLog) -> None:
    # Loop guard: a fix PR's own CI failing must never trigger another autofix run.
    for branch in (cfg.head_branch, cfg.base_branch):
        if branch.startswith("autofix/"):
            return _stop(state, Outcome.skipped, f"branch {branch} is an autofix branch")
    if git(cfg.repo, "status", "--porcelain", "--untracked-files=no").strip():
        return _stop(state, Outcome.skipped, "working tree has uncommitted changes; refusing to run")
    deadline = Deadline(cfg.timeout_s)

    collect(cfg, state, log)
    assert state.initial is not None
    if state.initial.passed:
        return _stop(state, Outcome.nothing_to_fix, "tests pass at this commit")

    classify(cfg, state, llm, log, timeout_s=deadline.left())
    c = state.classification
    if c is None:
        return _stop(state, Outcome.reported, state.reason or "classification failed")
    if state.rerun_passed:
        # Deterministic evidence beats the model's opinion: a test that passes on re-run is not
        # a stable failure, so no code change.
        return _stop(state, Outcome.reported, "failing test passed on re-run (flaky); no code change")
    if c.category in (Category.flaky, Category.infra):
        return _stop(state, Outcome.reported, f"classified as {c.category.value}; no code change")
    if c.confidence < cfg.min_confidence:
        return _stop(state, Outcome.reported,
                     f"confidence {c.confidence:.2f} < {cfg.min_confidence}; leaving it to a human")

    for n in range(1, cfg.max_fix_attempts + 1):
        if deadline.left() < 60:
            break
        if n > 1:
            reset_worktree(cfg)
        attempt = fix(cfg, state, llm, log, n, timeout_s=deadline.left())
        validate(cfg, state, attempt, log)
        state.attempts.append(attempt)
        log.event("attempt", "done", n=n, ok=attempt.ok, violations=attempt.guard_violations)
        if attempt.ok:
            return open_pr(cfg, state, attempt, log)

    reset_worktree(cfg)
    why = "timed out" if deadline.left() < 60 else f"no valid fix after {len(state.attempts)} attempt(s)"
    _stop(state, Outcome.reported, why)
