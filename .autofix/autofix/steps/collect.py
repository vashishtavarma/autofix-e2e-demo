"""Step 1 (deterministic): run the tests, capture a trimmed log, and gather commit context."""
from __future__ import annotations

import re
import subprocess
import time

from ..config import Config
from ..proc import child_env, git, run, split_command
from ..runlog import RunLog, scrub
from ..state import RunState, TestRun

TAIL_LINES = 200
MAX_LOG_CHARS = 30_000
MAX_DIFF_LINES = 400

_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)")
_SECTION_RE = re.compile(r"^=+ (.+?) =+$")


def parse_failing_tests(output: str) -> list[str]:
    """pytest's short summary lines -> node ids, e.g. 'tests/test_x.py::test_y'."""
    ids: list[str] = []
    for line in output.splitlines():
        if m := _SUMMARY_RE.match(line.strip()):
            node = m.group(2)
            if node not in ids:
                ids.append(node)
    return ids


def trim_log(output: str, tail: int = TAIL_LINES, max_chars: int = MAX_LOG_CHARS) -> str:
    """Keep the FAILURES/ERRORS sections (the actual tracebacks) plus the last `tail` lines.

    Everything else (collection noise, passing-test dots, warnings) is dropped so the model
    sees signal, not volume.
    """
    lines = output.splitlines()
    keep: set[int] = set(range(max(0, len(lines) - tail), len(lines)))
    in_section = False
    for i, line in enumerate(lines):
        if m := _SECTION_RE.match(line):
            in_section = m.group(1).strip() in ("FAILURES", "ERRORS")
        if in_section:
            keep.add(i)
    out: list[str] = []
    prev = -1
    for i in sorted(keep):
        if i != prev + 1:
            out.append(f"... [{i - prev - 1} lines trimmed] ...")
        out.append(lines[i])
        prev = i
    text = "\n".join(out)
    if len(text) > max_chars:
        text = "... [trimmed] ...\n" + text[-max_chars:]
    return text


def run_tests(cfg: Config, extra: list[str] | None = None) -> TestRun:
    argv = split_command(cfg.test_command) + (extra or [])
    shown = " ".join([cfg.test_command, *(extra or [])])
    start = time.monotonic()
    try:
        proc = run(argv, cwd=cfg.repo, timeout=cfg.test_timeout_s, env=child_env())
        output, code = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        output, code = f"{e.stdout or ''}\n[autofix] test command timed out after {cfg.test_timeout_s}s", 124
    return TestRun(command=shown, exit_code=code, output_tail=scrub(trim_log(output)),
                   failing_tests=parse_failing_tests(output),
                   duration_s=round(time.monotonic() - start, 2))


def collect(cfg: Config, state: RunState, log: RunLog) -> None:
    repo = cfg.repo
    state.sha = git(repo, "rev-parse", "HEAD").strip()
    state.commit_message = git(repo, "log", "-1", "--format=%B").strip()
    has_parent = run(["git", "rev-parse", "--verify", "-q", "HEAD~1"], cwd=repo).returncode == 0
    # A root commit has no parent to diff against; `git show` covers both cases.
    diff = git(repo, "diff", "HEAD~1", "HEAD") if has_parent else git(repo, "show", "--format=", "HEAD")
    diff_lines = diff.splitlines()
    if len(diff_lines) > MAX_DIFF_LINES:
        diff_lines = diff_lines[:MAX_DIFF_LINES] + [f"... [{len(diff_lines) - MAX_DIFF_LINES} more lines]"]
    state.last_commit_diff = scrub("\n".join(diff_lines))
    names = git(repo, "show", "--name-only", "--format=", "HEAD")
    state.files_touched = [n for n in names.splitlines() if n.strip()]

    state.initial = run_tests(cfg)
    log.event("collect", "tests", exit_code=state.initial.exit_code,
              failing=state.initial.failing_tests, duration_s=state.initial.duration_s)

    # Deterministic flakiness probe: re-run only the failures twice. Any pass => not a stable failure.
    if not state.initial.passed and state.initial.failing_tests:
        for i in range(2):
            rerun = run_tests(cfg, state.initial.failing_tests)
            log.event("collect", "rerun", n=i + 1, exit_code=rerun.exit_code)
            if rerun.passed:
                state.rerun_passed = True
                break
