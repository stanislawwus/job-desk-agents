"""Runs ONE agent on the Claude Agent SDK with exactly the MCP tools it is allowed to use.

Isolation, so that the only things an agent can do are the three MCP tools:
  tools=[]                 no built-in Claude Code tools (no shell, no file access)
  setting_sources=[]       no user / project settings, no CLAUDE.md
  strict_mcp_config=True   no MCP servers except the one defined here
  allowed_tools / disallowed_tools   the per-agent allow-list (the Scout never even sees `apply`)

The agent reaches the tools through an MCP client (the SDK spawns `python -m jobdesk.server` as a stdio subprocess per
agent, with that agent's AGENT_ROLE), so the server can enforce the write boundary a second time.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage, SystemMessage,
                              TextBlock, query)

from jobdesk.tracing import Tracer

ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "jobdesk"
ALL_TOOLS = ("search_jobs", "get_job", "apply")


def sdk_tool(name: str) -> str:
    return f"mcp__{SERVER_NAME}__{name}"


def short_tool(sdk_name: str) -> str:
    return sdk_name.removeprefix(f"mcp__{SERVER_NAME}__")


@dataclass
class AgentSpec:
    name: str                 # scout | reviewer | single_agent  (also used as AGENT_ROLE)
    system_prompt: str
    tools: list[str]          # subset of ALL_TOOLS
    output_schema: dict | None = None
    max_turns: int = 25


@dataclass
class RunContext:
    run_dir: Path
    tracer: Tracer
    server_env: dict[str, str]   # JOBDESK_* variables for the MCP server
    model: str | None = None


@dataclass
class AgentResult:
    text: str = ""
    structured: Any = None
    is_error: bool = False
    subtype: str = ""
    num_turns: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: float | None = None
    duration_ms: int = 0
    usage: dict | None = None
    model: str | None = None
    mcp_failed: bool = False


def _parse_tool_response(resp: Any) -> Any:
    """MCP results arrive as content blocks; return the JSON payload when there is one."""
    if isinstance(resp, dict) and "content" in resp:
        resp = resp["content"]
    if isinstance(resp, list):
        texts = [b.get("text", "") for b in resp if isinstance(b, dict) and b.get("type") == "text"]
        resp = "\n".join(texts) if texts else resp
    if isinstance(resp, str):
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            return resp
    return resp


async def run_agent(spec: AgentSpec, prompt: str, ctx: RunContext, round_no: int = 1) -> AgentResult:
    tracer, result = ctx.tracer, AgentResult()

    async def pre_tool(input_data, tool_use_id, _context):
        tracer.event("tool_call", spec.name, tool=short_tool(input_data["tool_name"]),
                     input=input_data.get("tool_input"), tool_use_id=tool_use_id)
        if not input_data["tool_name"].startswith(f"mcp__{SERVER_NAME}__"):   # second line of defence: only our server
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "Only the jobdesk MCP tools are available."}}
        return {}

    async def post_tool(input_data, tool_use_id, _context):
        out, name = _parse_tool_response(input_data.get("tool_response")), short_tool(input_data["tool_name"])
        error = out.get("error") if isinstance(out, dict) else None
        result.tool_calls.append({"tool": name, "input": input_data.get("tool_input"), "output": out})
        tracer.event("tool_result", spec.name, tool=name, error=error, output=out, tool_use_id=tool_use_id)
        return {}

    async def post_tool_failure(input_data, tool_use_id, _context):
        name = short_tool(input_data["tool_name"])
        result.tool_calls.append({"tool": name, "input": input_data.get("tool_input"), "output": {"error": "tool_failure"}})
        tracer.event("tool_result", spec.name, tool=name, error="tool_failure",
                     output={"message": input_data.get("error")}, tool_use_id=tool_use_id)
        return {}

    env = {**ctx.server_env, "AGENT_ROLE": spec.name, "PYTHONPATH": str(ROOT),
           "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    options = ClaudeAgentOptions(
        system_prompt=spec.system_prompt,
        mcp_servers={SERVER_NAME: {"type": "stdio", "command": sys.executable, "args": ["-m", "jobdesk.server"], "env": env}},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=[sdk_tool(t) for t in spec.tools],
        disallowed_tools=[sdk_tool(t) for t in ALL_TOOLS if t not in spec.tools],
        permission_mode="dontAsk",
        setting_sources=[],
        extra_args={"no-chrome": None},   # some Claude Code installs attach the Chrome MCP server even with strict_mcp_config
        max_turns=spec.max_turns,
        model=ctx.model,
        cwd=str(ctx.run_dir),
        output_format={"type": "json_schema", "schema": spec.output_schema} if spec.output_schema else None,
        hooks={"PreToolUse": [HookMatcher(matcher="mcp__.*", hooks=[pre_tool])],
               "PostToolUse": [HookMatcher(matcher="mcp__.*", hooks=[post_tool])],
               "PostToolUseFailure": [HookMatcher(matcher="mcp__.*", hooks=[post_tool_failure])]},
    )

    tracer.event("agent_start", spec.name, round=round_no, tools=spec.tools, prompt_chars=len(prompt))
    texts: list[str] = []
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            visible = [t for t in msg.data.get("tools", []) if t.startswith("mcp__")]
            foreign = [t for t in visible if not t.startswith(f"mcp__{SERVER_NAME}__")]
            statuses = [s.get("status") for s in msg.data.get("mcp_servers", [])]
            result.model = msg.data.get("model")
            tracer.event("agent_init", spec.name, model=result.model, visible_tools=[short_tool(t) for t in visible if t not in foreign],
                         mcp=statuses)
            if foreign:
                tracer.event("warning", spec.name, message=f"{len(foreign)} tools from other MCP servers are visible to the agent; "
                                                           "their calls are denied by the hook")
            if not statuses or any(s != "connected" for s in statuses):
                result.mcp_failed = True
                tracer.event("error", spec.name, message="MCP server 'jobdesk' did not connect", mcp=statuses)
                break
        elif isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    texts.append(b.text)
                    tracer.event("assistant_text", spec.name, text=b.text)
        elif isinstance(msg, ResultMessage):
            result.is_error, result.subtype = msg.is_error, msg.subtype
            result.num_turns, result.cost_usd = msg.num_turns, msg.total_cost_usd
            result.duration_ms, result.usage = msg.duration_ms, msg.usage
            result.structured = msg.structured_output
            result.text = msg.result or "\n".join(texts)
    if not result.mcp_failed:
        tracer.event("agent_end", spec.name, round=round_no, num_turns=result.num_turns, tool_calls=len(result.tool_calls),
                     duration_ms=result.duration_ms, cost_usd=result.cost_usd, is_error=result.is_error,
                     subtype=result.subtype, usage=result.usage, structured=result.structured)
    return result
