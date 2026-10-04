"""Typed pipeline state. Everything a step produces lands here, so the run is inspectable after the fact."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class Category(StrEnum):
    real_bug = "real_bug"
    test_outdated = "test_outdated"
    flaky = "flaky"
    infra = "infra"


class Classification(BaseModel):
    category: Category
    confidence: float = Field(ge=0, le=1)
    suspected_files: list[str] = []
    root_cause: str


class FixResult(BaseModel):
    """What the fix model claims it did. Never trusted for correctness; validate re-checks."""
    summary: str = Field(description="One-line summary, imperative mood, <= 70 chars")
    root_cause: str
    what_changed: str
    why_correct: str
    files_changed: list[str] = []


class TestRun(BaseModel):
    __test__ = False  # not a pytest test class, despite the name
    command: str
    exit_code: int
    output_tail: str
    failing_tests: list[str] = []
    duration_s: float = 0.0

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class Attempt(BaseModel):
    n: int
    fix: FixResult | None = None
    diff: str = ""
    guard_violations: list[str] = []
    targeted: TestRun | None = None
    full: TestRun | None = None
    turns: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (not self.guard_violations and self.error is None
                and self.targeted is not None and self.targeted.passed
                and self.full is not None and self.full.passed)


class Outcome(StrEnum):
    pr_opened = "pr_opened"
    pr_dry_run = "pr_dry_run"
    reported = "reported"          # no code change; explanation only
    nothing_to_fix = "nothing_to_fix"
    skipped = "skipped"            # loop guard etc.


class RunState(BaseModel):
    run_id: str
    repo: str
    sha: str = ""
    base_branch: str = ""
    commit_message: str = ""
    last_commit_diff: str = ""
    files_touched: list[str] = []
    initial: TestRun | None = None
    rerun_passed: bool = False     # deterministic flakiness signal
    classification: Classification | None = None
    classify_turns: int = 0
    attempts: list[Attempt] = []
    outcome: Outcome | None = None
    reason: str = ""
    pr_branch: str = ""
    pr_title: str = ""
    pr_body: str = ""
    pr_url: str = ""
    cost_usd: float = 0.0
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
