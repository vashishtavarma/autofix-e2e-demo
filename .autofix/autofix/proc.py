"""Subprocess helpers shared by the deterministic steps."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from .runlog import SECRET_ENV_VARS


def child_env() -> dict[str, str]:
    """Env for running the target repo's tests: no credentials, and our interpreter first on PATH."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV_VARS}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def split_command(command: str) -> list[str]:
    argv = shlex.split(command)
    # Windows resolves argv[0] against the *parent's* PATH, so pin "python" explicitly.
    if argv and argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    return argv


def run(argv: list[str], cwd: Path, timeout: float | None = None, check: bool = False,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=check, env=env)


def git(repo: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], cwd=repo, check=check).stdout
