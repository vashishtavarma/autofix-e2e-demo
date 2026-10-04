"""Safety checks enforced in code, not prompts.

Two layers use these rules:
  * check_tool_call() runs live inside the LLM session (PreToolUse hook) and denies bad tool calls.
  * check_diff() runs on the final git diff before validation; it is the authoritative gate.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .state import Category

PROTECTED_DIRS = (".github/", ".gitlab/", ".circleci/", ".git/", ".autofix/")
PROTECTED_FILES = {
    ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile", ".travis.yml",
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock",
}
SECRET_NAME_RE = re.compile(r"(^\.env(\..*)?$|\.pem$|\.key$|^id_(rsa|ed25519)|secret|credential)", re.I)

# Markers that silence a test instead of fixing code. Checked on every added line in every file.
SKIP_RE = re.compile(
    r"(pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail|importorskip)\s*\("
    r"|unittest\.skip|@skip\b|\b(it|test|describe)\.skip\s*\(|\bxit\s*\(|--deselect|--ignore)"
)
# Source code that detects it is under test is a classic way to "pass" without fixing anything.
TEST_DETECT_RE = re.compile(r"PYTEST_CURRENT_TEST|['\"]pytest['\"]\s+in\s+sys\.modules|sys\.modules\[['\"]pytest")
ASSERT_RE = re.compile(r"\bassert\w*\b|pytest\.raises|\bexpect\s*\(")
TEST_DEF_RE = re.compile(r"^\s*(async\s+)?def\s+(test\w*)\s*\(")


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    name = p.name
    return (
        name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
        or any(part in ("tests", "test", "__tests__") for part in p.parts[:-1])
        or bool(re.search(r"\.(test|spec)\.[jt]sx?$", name))
    )


def protected_reason(path: str) -> str | None:
    """Why an edit to this repo-relative path is never allowed, or None."""
    rel = path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if rel.startswith("/") or ".." in PurePosixPath(rel).parts or re.match(r"^[A-Za-z]:", rel):
        return f"{path}: outside the repository"
    for d in PROTECTED_DIRS:
        if rel.startswith(d) or f"/{d}" in f"/{rel}":
            return f"{path}: CI/VCS/agent directory {d} is protected"
    name = PurePosixPath(rel).name
    if name in PROTECTED_FILES:
        return f"{path}: CI config / lockfile is protected"
    if SECRET_NAME_RE.search(name):
        return f"{path}: looks like a secrets file"
    return None


def edit_reason(path: str, category: Category | None) -> str | None:
    if reason := protected_reason(path):
        return reason
    if is_test_file(path) and category != Category.test_outdated:
        return f"{path}: tests are the spec; test files may only change when classification is test_outdated"
    return None


# --------------------------------------------------------------------------- diff checks

@dataclass
class FileDiff:
    path: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    deleted: bool = False


def parse_diff(diff: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            # "diff --git a/x b/x" -> take the b/ side (the post-change path)
            path = line.split(" b/", 1)[-1] if " b/" in line else line.split()[-1]
            cur = FileDiff(path=path)
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("deleted file mode"):
            cur.deleted = True
        elif line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            cur.added.append(line[1:])
        elif line.startswith("-"):
            cur.removed.append(line[1:])
    return files


def check_diff(diff: str, category: Category | None, max_lines: int) -> list[str]:
    """Return human-readable violations; empty list means the diff may proceed to testing."""
    files = parse_diff(diff)
    if not files:
        return ["empty diff: no changes were made"]
    violations: list[str] = []
    total = sum(len(f.added) + len(f.removed) for f in files)
    if total > max_lines:
        violations.append(f"diff too large: {total} changed lines > limit {max_lines}")

    for f in files:
        if reason := edit_reason(f.path, category):
            violations.append(reason)
        if f.deleted and is_test_file(f.path):
            violations.append(f"{f.path}: deleting a test file is not allowed")
        for line in f.added:
            if SKIP_RE.search(line):
                violations.append(f"{f.path}: adds a skip/xfail/deselect marker: {line.strip()[:80]}")
            if not is_test_file(f.path) and TEST_DETECT_RE.search(line):
                violations.append(f"{f.path}: source code detects the test runner: {line.strip()[:80]}")
        if is_test_file(f.path):
            removed_tests = {m.group(2) for l in f.removed if (m := TEST_DEF_RE.match(l))}
            added_tests = {m.group(2) for l in f.added if (m := TEST_DEF_RE.match(l))}
            if gone := sorted(removed_tests - added_tests):
                violations.append(f"{f.path}: deletes test function(s) {', '.join(gone)}")
            n_removed = sum(bool(ASSERT_RE.search(l)) for l in f.removed)
            n_added = sum(bool(ASSERT_RE.search(l)) for l in f.added)
            if n_removed > n_added:
                violations.append(f"{f.path}: removes {n_removed - n_added} assertion(s)")
    return violations


# --------------------------------------------------------------------------- live tool-call checks

# Flags the fix model may append to the test command. Anything else (e.g. --basetemp, -p, -c)
# could delete files or load arbitrary plugins.
SAFE_TEST_FLAGS = {"-q", "-qq", "-x", "-v", "-vv", "-s", "-l", "-ra", "-rA", "--no-header",
                   "--tb=short", "--tb=long", "--tb=line", "--tb=no", "--maxfail=1", "--lf"}
SHELL_META_RE = re.compile(r"[;&|`$<>(){}\n\\]")


def bash_reason(command: str, test_command: str) -> str | None:
    """Only the configured test command (+ safe flags / test ids) may run."""
    cmd = command.strip().replace("\\", "/")  # Windows-style test paths
    if SHELL_META_RE.search(cmd):
        return "shell operators are not allowed; run only the test command"
    try:
        given, allowed = shlex.split(cmd), shlex.split(test_command)
    except ValueError:
        return "could not parse command"
    if given[: len(allowed)] != allowed:
        return f"only the test command is allowed: `{test_command} [test ids]`"
    for tok in given[len(allowed):]:
        if tok.startswith("-"):
            if tok not in SAFE_TEST_FLAGS:
                return f"flag {tok} is not allowed"
        elif protected_reason(tok.split("::")[0]):
            return f"test path {tok} is outside the repo"
    return None


def path_inside(repo: Path, path: str) -> str | None:
    """Absolute or relative tool path -> repo-relative posix path, or None if outside."""
    p = Path(path)
    full = (p if p.is_absolute() else repo / p).resolve()
    try:
        return full.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}


def check_tool_call(tool: str, args: dict, repo: Path, category: Category | None,
                    test_command: str | None, allow_edits: bool) -> str | None:
    """Deny reason for a tool call, or None to allow."""
    if tool in READ_TOOLS:
        path = args.get("file_path") or args.get("path")
        if not path:
            return None  # Grep/Glob default to cwd, which is the repo
        rel = path_inside(repo, path)
        if rel is None:
            return f"{path} is outside the repository"
        if rel == ".git" or rel.startswith(".git/"):
            return ".git/ is off limits"
        return None
    if tool in EDIT_TOOLS:
        if not allow_edits:
            return "this step is read-only"
        path = args.get("file_path") or args.get("notebook_path") or ""
        rel = path_inside(repo, path)
        if rel is None:
            return f"{path} is outside the repository"
        return edit_reason(rel, category)
    if tool == "Bash":
        if not test_command:
            return "Bash is not available in this step"
        return bash_reason(args.get("command", ""), test_command)
    return f"tool {tool} is not allowed"
