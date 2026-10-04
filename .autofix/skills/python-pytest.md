# Skill: Python + pytest

## Reading pytest output
- `FAILED path::test_name - AssertionError: ...` in the short summary names each failure.
- In the `FAILURES` section, lines starting with `>` mark the failing line and `E` lines show
  the evaluated values (`E  assert 9 == 10` means actual 9, expected 10).
- `ERROR` (not `FAILED`) during collection usually means an import error or a broken fixture —
  look at the first `ImportError` / `ModuleNotFoundError` in the traceback.
- `ModuleNotFoundError` for a **third-party** package (not a module in this repo) is almost
  always `infra`. For a module that exists in this repo, it is a real bug (bad import path).

## Common real bugs
- Off-by-one in `range()`, slicing (`items[1:]`, `items[:-1]`), or `<` vs `<=`.
- Mutable default arguments (`def f(x=[])`).
- Integer vs float division, rounding, or truncation (`int()` vs `round()`).
- Wrong variable used after a rename; returning inside a loop.

## Flakiness signals
- `random` without a seed, `time.time()`, `datetime.now()`, `uuid4()`, dict/set ordering
  assumptions, `sleep`-based waits, tests depending on execution order.
- The right response to flakiness is a report, not an edit.

## Running tests
- Run a single test with the configured test command plus its node id, e.g.
  `python -m pytest -q tests/test_x.py::test_name`.
- Only the configured test command (plus test ids and `-q/-x/-v/--tb=...`) is permitted.
