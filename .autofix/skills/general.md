# Skill: fixing failing tests (general)

## Mindset
- The failing test output is your primary evidence. Read the assertion, the traceback, and the
  code under test before forming a theory.
- The most recent commit is the most likely cause. Its message and diff are provided — check
  whether the failure lines up with what that commit changed.
- Tests are the specification. Unless there is clear evidence the *intended* behavior changed
  (commit message, docs, changelog say so), assume the code is wrong, not the test.

## Classifying a failure
- `real_bug`: the source code violates behavior the tests correctly describe.
- `test_outdated`: the behavior was changed **intentionally** (the commit message / docs say so)
  and the test still asserts the old behavior. Requires explicit evidence of intent.
- `flaky`: outcome depends on randomness, wall-clock time, ordering, concurrency, or timing.
  Signs: `random`, `time.time()`, `datetime.now()`, `sleep`, shared global state, or a test
  that passed when re-run.
- `infra`: the environment is broken — missing/incompatible dependency, import of a package
  that is not installed, network/service unavailable, CI misconfiguration. Code logic is fine.
- When unsure, lower your confidence. Low confidence produces a report for a human, which is
  always acceptable; a wrong code change is not.

## Making a fix
- Make the **smallest** change that makes the failing tests pass for the right reason.
- Fix the root cause, not the symptom. Do not special-case the inputs used by the test.
- Never weaken tests: no deleting tests or assertions, no skip/xfail markers, no loosening
  expected values unless the classification is `test_outdated` and the new value is the
  documented intended behavior.
- Do not touch CI configuration, lockfiles, dependency pins, or unrelated code. No refactors,
  no formatting changes, no new features.
- After editing, run the failing tests, then the full suite, using the allowed test command.

## Writing the explanation
- Root cause: one or two sentences, concrete (file, function, what was wrong).
- Why this is the right fix: tie the change to the intended behavior described by the tests
  or the commit message.
