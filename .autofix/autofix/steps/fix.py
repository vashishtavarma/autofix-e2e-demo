"""Step 3 (LLM, strong model): edit the code. Its claims are recorded, never trusted — validate decides."""
from __future__ import annotations

from pydantic import ValidationError

from ..config import Config
from ..guards import check_tool_call
from ..llm import LLM, AgentRequest, parse_json_output
from ..runlog import RunLog
from ..skills import load_skills
from ..state import Attempt, Category, FixResult, RunState

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "imperative one-liner, <= 70 chars, no trailing period"},
        "root_cause": {"type": "string"},
        "what_changed": {"type": "string"},
        "why_correct": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "root_cause", "what_changed", "why_correct", "files_changed"],
    "additionalProperties": False,
}

SYSTEM = """You are the fix step of an automated CI-repair pipeline.
Tools: Read, Grep, Glob, Edit, and Bash restricted to the project's test command.
Bash already runs in the repository root: call the test command directly, one command per call,
with no `cd`, `&&`, pipes, or other shell operators.
Make the smallest correct change that fixes the root cause. Every tool call is checked by
code-level guards; a denied call means that approach is not allowed - choose another.
When done, return the structured output describing your fix."""


def _feedback(prev: list[Attempt]) -> str:
    if not prev:
        return ""
    parts = ["## Previous attempts (all rejected; the working tree was reset to the original commit)"]
    for a in prev:
        parts.append(f"### Attempt {a.n}")
        if a.error:
            parts.append(f"Error: {a.error}")
        if a.guard_violations:
            parts.append("Rejected by guards:\n" + "\n".join(f"- {v}" for v in a.guard_violations))
        for label, run in (("Failing tests after the change", a.targeted), ("Full suite after the change", a.full)):
            if run is not None and not run.passed:
                parts.append(f"{label} still failed:\n```\n{run.output_tail[-4000:]}\n```")
        if a.diff:
            parts.append(f"Diff that was tried:\n```diff\n{a.diff[:4000]}\n```")
    return "\n\n".join(parts)


def build_prompt(cfg: Config, state: RunState, prev: list[Attempt]) -> str:
    c, initial = state.classification, state.initial
    assert c is not None and initial is not None
    tests_rule = ("Test files MAY be updated, because the behavior change was intentional; keep every test "
                  "and assertion, only update expected values to the new intended behavior."
                  if c.category == Category.test_outdated else
                  "Test files must NOT be modified. Fix the source code.")
    return f"""## Diagnosis from triage
Category: {c.category.value} (confidence {c.confidence:.2f})
Root cause: {c.root_cause}
Suspected files: {', '.join(c.suspected_files) or '(none given)'}

## Failing tests
{', '.join(initial.failing_tests) or '(see output)'}

```
{initial.output_tail}
```

## Last commit
```
{state.commit_message}
```
```diff
{state.last_commit_diff}
```

## Rules
- {tests_rule}
- Never add skip/xfail markers, delete tests or assertions, or detect the test runner in code.
- Do not touch .github/, CI config, lockfiles, or dependency pins.
- Keep the diff small (hard limit {cfg.max_diff_lines} changed lines).
- Verify with: `{cfg.test_command} <test ids>` and then `{cfg.test_command}`.

{_feedback(prev)}"""


def fix(cfg: Config, state: RunState, llm: LLM, log: RunLog, n: int, timeout_s: float) -> Attempt:
    category = state.classification.category if state.classification else None
    req = AgentRequest(
        step=f"fix#{n}",
        prompt=build_prompt(cfg, state, state.attempts),
        system_prompt=SYSTEM + "\n\n" + load_skills(cfg),
        model=cfg.fix_model,
        max_turns=cfg.fix_max_turns,
        tools=["Read", "Grep", "Glob", "Edit", "Bash"],
        cwd=cfg.repo,
        tool_guard=lambda tool, args: check_tool_call(tool, args, cfg.repo, category, cfg.test_command,
                                                      allow_edits=True),
        output_schema=SCHEMA,
        timeout_s=timeout_s,
    )
    res = llm.run(req, log)
    state.cost_usd += res.cost_usd
    attempt = Attempt(n=n, turns=res.turns, cost_usd=res.cost_usd)
    try:
        attempt.fix = FixResult.model_validate(parse_json_output(res))
    except ValidationError:
        # Edits may still be good even if the summary is missing; validate judges the diff.
        attempt.fix = None
    if res.error and attempt.fix is None:
        attempt.error = res.error
    return attempt
