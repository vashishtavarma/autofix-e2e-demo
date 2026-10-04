"""Thin wrapper around the Claude Agent SDK. Steps depend on the `LLM` protocol so tests can mock it."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from .runlog import RunLog

ToolGuard = Callable[[str, dict[str, Any]], "str | None"]

# The LLM never needs GitHub credentials; only the deterministic open_pr step does.
_HIDDEN_DURING_LLM = ("GH_TOKEN", "GITHUB_TOKEN")


@dataclass
class AgentRequest:
    step: str
    prompt: str
    system_prompt: str
    model: str
    max_turns: int
    tools: list[str]
    cwd: Path
    tool_guard: ToolGuard
    output_schema: dict[str, Any] | None = None
    timeout_s: float | None = None


@dataclass
class AgentResult:
    text: str = ""
    structured: Any = None
    turns: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class LLM(Protocol):
    def run(self, req: AgentRequest, log: RunLog) -> AgentResult: ...


@contextlib.contextmanager
def _hide_env(names: tuple[str, ...]) -> Iterator[None]:
    saved = {n: os.environ.pop(n) for n in names if n in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _brief(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in args.items()}


class ClaudeAgentLLM:
    def run(self, req: AgentRequest, log: RunLog) -> AgentResult:
        with _hide_env(_HIDDEN_DURING_LLM):
            try:
                return asyncio.run(asyncio.wait_for(self._run(req, log), req.timeout_s))
            except TimeoutError:
                return AgentResult(error=f"timed out after {req.timeout_s:.0f}s")

    async def _run(self, req: AgentRequest, log: RunLog) -> AgentResult:
        # Imported lazily so unit tests (which mock this class) don't need the SDK/CLI.
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher,
                                      ResultMessage, TextBlock, query)

        async def pre_tool_use(hook_input: dict[str, Any], _tool_use_id: str | None, _ctx: Any) -> dict[str, Any]:
            name, args = hook_input["tool_name"], hook_input.get("tool_input") or {}
            if name == "StructuredOutput":  # the SDK's channel for output_format; always allowed
                return {}
            reason = req.tool_guard(name, args)
            log.event(req.step, "tool", tool=name, input=_brief(args), denied=reason)
            if reason:
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                               "permissionDecision": "deny",
                                               "permissionDecisionReason": reason}}
            return {}

        options = ClaudeAgentOptions(
            model=req.model,
            system_prompt=req.system_prompt,
            max_turns=req.max_turns,
            cwd=str(req.cwd),
            tools=req.tools,                 # the only built-in tools that exist in this session
            allowed_tools=req.tools,         # ...pre-approved, but every call still passes the hook
            permission_mode="dontAsk",       # anything not approved is denied, never prompted
            setting_sources=[],              # ignore user/repo .claude settings (could add allow rules or hooks)
            # Same interpreter for the model's test runs as for ours (matters with venvs / multiple Pythons).
            env={"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")},
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])]},
            output_format={"type": "json_schema", "schema": req.output_schema} if req.output_schema else None,
        )
        result = AgentResult()
        try:
            async for msg in query(prompt=req.prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            log.event(req.step, "assistant", text=block.text[:500])
                elif isinstance(msg, ResultMessage):
                    result.turns = msg.num_turns
                    result.cost_usd = msg.total_cost_usd or 0.0
                    result.text = msg.result or ""
                    result.structured = msg.structured_output
                    if msg.is_error:
                        result.error = f"{msg.subtype}: {(msg.result or '')[:300]} {msg.errors or ''}".strip()
        except Exception as e:  # SDK raises on error results (auth, max turns); keep what we have
            result.error = result.error or f"{type(e).__name__}: {str(e)[:300]}"
        log.event(req.step, "llm_done", turns=result.turns, cost_usd=result.cost_usd, error=result.error)
        return result


def parse_json_output(result: AgentResult) -> Any:
    """Prefer SDK structured output; fall back to the last {...} block in the final text."""
    if result.structured is not None:
        return result.structured
    text = result.text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
