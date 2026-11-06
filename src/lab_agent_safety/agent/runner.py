"""Run the agent on one task: Claude Agent SDK -> our stdio MCP server.

What the harness controls (D4):
  - system prompt: our own string, replacing Claude Code's preset
  - tools: no built-in tools (tools=[]); only the lab's MCP tools exist
  - settings: setting_sources=[] so no local CLAUDE.md or settings leak in
  - approval: every *physical* MCP tool goes through can_use_tool -> Approver.
    Read-only tools are pre-approved. Physical tools must never be in allowed_tools,
    because pre-approved tools skip can_use_tool entirely.

  - isolation: strict_mcp_config, so no other MCP servers (user config, claude.ai
    connectors) can load; and the run aborts if the session's tool list is not exactly
    our tools, because a run with a stray tool would be an invalid data point

Everything the harness sees is written to <run_dir>/agent.jsonl.
"""

import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from lab_agent_safety.agent.approval import Approver
from lab_agent_safety.agent.prompts import system_prompt
from lab_agent_safety.config import LayerConfig
from lab_agent_safety.lab.bench import BenchSpec
from lab_agent_safety.lab.reference import load_references
from lab_agent_safety.tracing import TraceWriter

SERVER = "lab"
READ_ONLY_TOOLS = ["list_devices", "describe_device", "job_status"]
PHYSICAL_TOOLS = ["pick_up_tip", "drop_tip", "aspirate", "dispense", "read_plate"]

# Pre-approving the read-only tools is deliberate, so the SDK's warning that they skip
# can_use_tool is expected noise.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


def mcp_name(tool: str) -> str:
    return f"mcp__{SERVER}__{tool}"


EXPECTED_TOOLS = {mcp_name(t) for t in READ_ONLY_TOOLS + PHYSICAL_TOOLS}


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    run_dir: Path
    prompt: str
    layers: LayerConfig
    bench: BenchSpec
    model: str
    seed: int = 0
    max_turns: int = 40
    max_budget_usd: float = 1.0  # hard stop for a runaway run


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    subtype: str  # "success" or an error_* subtype from the SDK
    final_text: str
    num_turns: int
    duration_ms: int
    cost_usd: float | None


def build_options(spec: RunSpec, approver: Approver, trace: TraceWriter) -> ClaudeAgentOptions:
    refs = load_references()
    # Absolute paths: the agent process (and so the server) runs with cwd=run_dir, where
    # a relative path would resolve to the wrong place.
    run_dir = spec.run_dir.resolve()
    bench_file = run_dir / "bench.json"
    bench_file.write_text(spec.bench.model_dump_json(indent=2))

    server_args = [
        "-m", "lab_agent_safety.mcp_server.server",
        "--run-dir", str(run_dir),
        "--run-id", spec.run_id,
        "--enforce" if spec.layers.driver_enforcement else "--no-enforce",
        "--bench", str(bench_file),
        "--seed", str(spec.seed),
    ]

    async def can_use_tool(tool: str, tool_input: dict, context: ToolPermissionContext):
        decision = await approver.decide(tool, tool_input)
        trace.write("approval", tool=tool, input=tool_input, approved=decision.approved,
                    reason=decision.reason, approver=decision.approver, answer=decision.answer)
        if decision.approved:
            return PermissionResultAllow(updated_input=tool_input)
        return PermissionResultDeny(message=decision.reason)

    return ClaudeAgentOptions(
        model=spec.model,
        system_prompt=system_prompt(spec.layers.prompt_rules, refs["liquid_handler"], refs["plate_reader"]),
        mcp_servers={SERVER: {"type": "stdio", "command": sys.executable, "args": server_args}},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=[mcp_name(t) for t in READ_ONLY_TOOLS],
        can_use_tool=can_use_tool,
        setting_sources=[],
        max_turns=spec.max_turns,
        max_budget_usd=spec.max_budget_usd,
        cwd=str(run_dir),
    )


async def run_agent(spec: RunSpec, approver: Approver) -> RunSummary:
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    with TraceWriter(spec.run_dir / "agent.jsonl", spec.run_id, source="agent") as trace:
        options = build_options(spec, approver, trace)
        trace.write("run_started", model=spec.model, layers=spec.layers.model_dump(), prompt=spec.prompt,
                    system_prompt=options.system_prompt)
        final_text, result = "", None
        async for message in query(prompt=spec.prompt, options=options):
            match message:
                case SystemMessage(subtype="init"):
                    tools = set(message.data.get("tools") or [])
                    trace.write("session_init", tools=sorted(tools),
                                mcp_servers=message.data.get("mcp_servers"), model=message.data.get("model"))
                    if tools != EXPECTED_TOOLS:
                        trace.write("harness_error", unexpected=sorted(tools - EXPECTED_TOOLS),
                                    missing=sorted(EXPECTED_TOOLS - tools))
                        raise RuntimeError(
                            f"run {spec.run_id}: agent tool set is not exactly the lab tools "
                            f"(unexpected {sorted(tools - EXPECTED_TOOLS)}, missing {sorted(EXPECTED_TOOLS - tools)})"
                        )
                case AssistantMessage():
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            final_text = block.text
                            trace.write("assistant_text", text=block.text)
                        elif isinstance(block, ToolUseBlock):
                            # message_id groups the calls the model sent together in one
                            # turn (a "batch"); the grader needs it for the D17 metric.
                            trace.write("tool_call", tool_use_id=block.id, name=block.name, input=block.input,
                                        message_id=message.message_id)
                    trace.write("assistant_usage", usage=message.usage, message_id=message.message_id)
                case UserMessage() if isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            trace.write("tool_result", tool_use_id=block.tool_use_id,
                                        content=block.content, is_error=block.is_error)
                case ResultMessage():
                    result = message
                    trace.write("result", subtype=message.subtype, is_error=message.is_error,
                                num_turns=message.num_turns, duration_ms=message.duration_ms,
                                duration_api_ms=message.duration_api_ms, total_cost_usd=message.total_cost_usd,
                                usage=message.usage, model_usage=message.model_usage)
    if result is None:
        raise RuntimeError(f"run {spec.run_id} ended without a ResultMessage")
    return RunSummary(spec.run_id, result.subtype, final_text, result.num_turns, result.duration_ms,
                      result.total_cost_usd)
