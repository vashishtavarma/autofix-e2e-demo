"""JSON-lines run log: one record per step / tool call. Everything passes through scrub() first."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
# Known token shapes, so a secret leaked through test output is caught even if it isn't in our env.
_TOKEN_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
)


def scrub(text: str) -> str:
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value and len(value) >= 8:
            text = text.replace(value, f"[{name}]")
    return _TOKEN_RE.sub("[REDACTED]", text)


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {k: _scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_obj(v) for v in obj]
    return obj


class RunLog:
    def __init__(self, path: Path | None, echo: bool = True):
        self.path = path
        self.echo = echo
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, step: str, kind: str, **data: Any) -> None:
        record = _scrub_obj({"ts": round(time.time(), 3), "step": step, "kind": kind, **data})
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        if self.echo:
            brief = {k: v for k, v in record.items() if k not in ("ts", "step", "kind")}
            text = json.dumps(brief, default=str)
            print(f"[{step}] {kind} {text[:300]}", flush=True)
