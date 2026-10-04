"""Step 5 (deterministic): branch, commit, push, PR. Only ever pushes to autofix/*; never merges."""
from __future__ import annotations

import re

from ..config import Config
from ..proc import git, run
from ..runlog import RunLog, scrub
from ..state import Attempt, Outcome, RunState

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898283+github-actions[bot]@users.noreply.github.com"


def _tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])


def build_pr(cfg: Config, state: RunState, attempt: Attempt) -> tuple[str, str, str]:
    c, initial, fx = state.classification, state.initial, attempt.fix
    assert c and initial and attempt.full
    summary = (fx.summary if fx else f"repair {', '.join(initial.failing_tests) or 'failing tests'}").strip().rstrip(".")
    summary = re.sub(r"^fix(\(.*?\))?:\s*", "", summary, flags=re.I)  # "fix: ..." -> "..."
    summary = re.sub(r"^fix(es|ed)?\s+", "", summary, flags=re.I)     # "Fix off-by-one" -> "off-by-one"
    title = "fix: " + summary[:1].lower() + summary[1:]
    stat = git(cfg.repo, "diff", "--stat", "HEAD").strip()
    before = "\n".join(f"- `{t}`" for t in initial.failing_tests) or "- (see log)"
    body = f"""## Root cause
{fx.root_cause if fx else c.root_cause}

## What changed
{fx.what_changed if fx else '(see diff)'}

```
{stat}
```

## Why this is the right fix
{fx.why_correct if fx else '(not provided)'}

## Evidence
**Before** (commit `{state.sha[:7]}`, `{initial.command}` exit {initial.exit_code}) failing:
{before}

<details><summary>Failure output (trimmed)</summary>

```
{_tail(initial.output_tail, 40)}
```
</details>

**After** (re-run by the pipeline, not the model):
- failing tests: `{attempt.targeted.command if attempt.targeted else ''}` → exit {attempt.targeted.exit_code if attempt.targeted else '?'}
- full suite: `{attempt.full.command}` → exit {attempt.full.exit_code}

```
{_tail(attempt.full.output_tail, 15)}
```

## Classification
`{c.category.value}` · confidence {c.confidence:.2f} · fix attempt {attempt.n}/{cfg.max_fix_attempts} · ~${state.cost_usd:.2f}

---
_Opened automatically by autofix-agent — please review._"""
    return f"autofix/{state.sha[:7]}", title[:100], scrub(body)


def open_pr(cfg: Config, state: RunState, attempt: Attempt, log: RunLog) -> None:
    branch, title, body = build_pr(cfg, state, attempt)
    # Belt and braces: these are invariants, checked in code right before anything is pushed.
    if not branch.startswith("autofix/") or branch == cfg.base_branch:
        raise RuntimeError(f"refusing to push to {branch}")
    state.pr_branch, state.pr_title, state.pr_body = branch, title, body
    body_file = cfg.runs_dir / f"{state.run_id}.pr.md"
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(body, encoding="utf-8")

    if cfg.dry_run:
        print(f"\n=== DRY RUN: would open PR ===\nbranch: {branch}\nbase:   {cfg.base_branch}\n"
              f"title:  {title}\n\n{body}\n=== diff ===\n{attempt.diff}", flush=True)
        state.outcome = Outcome.pr_dry_run
        state.reason = "fix validated (dry run, nothing pushed)"
        log.event("open_pr", "dry_run", branch=branch, title=title)
        return

    git(cfg.repo, "checkout", "-q", "-B", branch)
    git(cfg.repo, "add", "-u")  # only files that already existed; the fix step cannot create files
    git(cfg.repo, "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
        "commit", "-q", "-m", title, "-m", f"Automated fix for {state.sha[:7]}. See PR for details.")
    # Force is fine: the branch is ours and named after the failing SHA.
    git(cfg.repo, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
    proc = run(["gh", "pr", "create", "--base", cfg.base_branch, "--head", branch,
                "--title", title, "--body-file", str(body_file)], cwd=cfg.repo)
    if proc.returncode != 0:
        # Most likely a PR for this branch already exists (re-run); reuse it.
        view = run(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], cwd=cfg.repo)
        if view.returncode != 0:
            raise RuntimeError(f"gh pr create failed: {scrub(proc.stderr)[:500]}")
        proc = view
    state.pr_url = proc.stdout.strip().splitlines()[-1]
    state.outcome = Outcome.pr_opened
    state.reason = "fix validated and PR opened"
    log.event("open_pr", "opened", url=state.pr_url, branch=branch)
