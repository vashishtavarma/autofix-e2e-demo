"""Step 2 (LLM, cheap model, read-only): decide *whether* this failure should get a code fix."""
from __future__ import annotations

from pydantic import ValidationError

from ..config import Config
from ..guards import check_tool_call
from ..llm import LLM, AgentRequest, parse_json_output
from ..runlog import RunLog
from ..skills import load_skills
from ..state import Category, Classification, RunState

# Hand-written (not model_json_schema()) to keep it flat: no $defs/$ref for the CLI to resolve.
SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "suspected_files": {"type": "array", "items": {"type": "string"}},
        "root_cause": {"type": "string"},
    },
    "required": ["category", "confidence", "suspected_files", "root_cause"],
    "additionalProperties": False,
}

SYSTEM = """You are the triage step of an automated CI-repair pipeline.
You can only read the repository (Read, Grep, Glob). You cannot edit or run anything.
Your job: classify why the test suite failed, so the pipeline can decide whether an automated
code fix is appropriate. Investigate briefly and precisely; do not propose a patch.
Return your answer as the structured output object."""


def build_prompt(state: RunState) -> str:
    assert state.initial is not None
    rerun = ("YES - at least one re-run of the failing tests PASSED (strong flakiness signal)"
             if state.rerun_passed else "no - the failures reproduced on re-run")
    return f"""## Failing test run
Command: `{state.initial.command}` (exit code {state.initial.exit_code})
Failing tests: {', '.join(state.initial.failing_tests) or '(could not parse; see output)'}
Passed on re-run: {rerun}

```
{state.initial.output_tail}
```

## Last commit
Message:
```
{state.commit_message}
```
Files touched: {', '.join(state.files_touched)}
Diff:
```diff
{state.last_commit_diff}
```

## Task
Read the relevant source and test files, then classify the failure as one of
real_bug | test_outdated | flaky | infra, with a confidence between 0 and 1,
the files you suspect need changing, and a concrete root cause (1-3 sentences)."""


def classify(cfg: Config, state: RunState, llm: LLM, log: RunLog, timeout_s: float) -> None:
    req = AgentRequest(
        step="classify",
        prompt=build_prompt(state),
        system_prompt=SYSTEM + "\n\n" + load_skills(cfg),
        model=cfg.classify_model,
        max_turns=cfg.classify_max_turns,
        tools=["Read", "Grep", "Glob"],
        cwd=cfg.repo,
        tool_guard=lambda tool, args: check_tool_call(tool, args, cfg.repo, None, None, allow_edits=False),
        output_schema=SCHEMA,
        timeout_s=timeout_s,
    )
    res = llm.run(req, log)
    state.cost_usd += res.cost_usd
    state.classify_turns = res.turns
    data = parse_json_output(res)
    try:
        state.classification = Classification.model_validate(data)
    except ValidationError as e:
        state.classification = None
        state.reason = f"classification failed: {res.error or 'invalid output'} ({str(e)[:200]})"
    log.event("classify", "result", classification=state.classification and state.classification.model_dump(),
              error=res.error)
