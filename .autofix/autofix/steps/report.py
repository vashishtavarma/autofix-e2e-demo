"""Step 6 (deterministic): explain the outcome to humans. Runs for every outcome; comments only when no PR."""
from __future__ import annotations

import os

from ..config import Config
from ..proc import run
from ..runlog import RunLog, scrub
from ..state import Outcome, RunState


def build_report(state: RunState) -> str:
    c = state.classification
    lines = [f"## autofix-agent: `{state.outcome.value if state.outcome else 'unknown'}`", "",
             f"**Reason:** {state.reason}", ""]
    if state.pr_url:
        lines += [f"**PR:** {state.pr_url}", ""]
    if state.initial:
        lines += [f"**Failing tests at `{state.sha[:7]}`:** " + (", ".join(f"`{t}`" for t in state.initial.failing_tests) or "(none parsed)"),
                  f"**Passed on re-run:** {'yes' if state.rerun_passed else 'no'}", ""]
    if c:
        lines += [f"**Classification:** `{c.category.value}` (confidence {c.confidence:.2f})",
                  f"**Root cause:** {c.root_cause}", ""]
    if state.attempts:
        lines += ["| attempt | guards | failing tests | full suite | turns |", "|---|---|---|---|---|"]
        for a in state.attempts:
            g = "ok" if not a.guard_violations else "; ".join(a.guard_violations)[:120]
            t = "-" if a.targeted is None else ("pass" if a.targeted.passed else "fail")
            f = "-" if a.full is None else ("pass" if a.full.passed else "fail")
            lines.append(f"| {a.n} | {g} | {t} | {f} | {a.turns} |")
        lines.append("")
    lines.append(f"_run `{state.run_id}` · cost ~${state.cost_usd:.2f}_")
    return scrub("\n".join(lines))


def report(cfg: Config, state: RunState, log: RunLog) -> str:
    md = build_report(state)
    print("\n" + md + "\n", flush=True)
    out = cfg.runs_dir / f"{state.run_id}.report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if (not cfg.dry_run and repo and state.sha
            and state.outcome == Outcome.reported):
        proc = run(["gh", "api", f"repos/{repo}/commits/{state.sha}/comments", "-f", f"body={md}"], cwd=cfg.repo)
        log.event("report", "commit_comment", ok=proc.returncode == 0)
    return md
