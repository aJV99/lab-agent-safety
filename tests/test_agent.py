"""Agent harness wiring, tested offline (no API calls)."""

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from lab_agent_safety.agent.approval import AutoApprover, CliApprover, Decision
from lab_agent_safety.agent.prompts import BASE_PROMPT, system_prompt
from lab_agent_safety.agent.runner import PHYSICAL_TOOLS, READ_ONLY_TOOLS, RunSpec, build_options, mcp_name
from lab_agent_safety.config import CONFIGS
from lab_agent_safety.lab.bench import load_bench_spec
from lab_agent_safety.lab.reference import load_references
from lab_agent_safety.mcp_server.server import build_server
from lab_agent_safety.mcp_server.server import LabConfig
from lab_agent_safety.tracing import TraceWriter, read_trace


def refs():
    r = load_references()
    return r["liquid_handler"], r["plate_reader"]


def test_base_prompt_carries_no_rules():
    prompt = system_prompt(False, *refs())
    assert prompt == BASE_PROMPT
    assert "200" not in prompt and "restricted" not in prompt


def test_rules_prompt_contains_every_limit_and_interlock():
    lh, reader = refs()
    prompt = system_prompt(True, lh, reader)
    assert prompt.startswith(BASE_PROMPT)
    for ref in (lh, reader):
        for key, value in ref.limits.model_dump().items():
            assert f"{key}: {value}" in prompt
        for interlock in ref.interlocks:
            assert interlock.id in prompt


def test_prompt_is_deterministic():
    assert system_prompt(True, *refs()) == system_prompt(True, *refs())  # cacheable prefix


class FakeApprover:
    def __init__(self, approve: bool):
        self.approve, self.seen = approve, []

    async def decide(self, tool, tool_input):
        self.seen.append(tool)
        return Decision(self.approve, "test", "fake")


def make_options(tmp_path, approver, config="driver_only"):
    spec = RunSpec(run_id="t", run_dir=tmp_path, prompt="p", layers=CONFIGS[config],
                   bench=load_bench_spec(), model="claude-haiku-5-5")
    trace = TraceWriter(tmp_path / "agent.jsonl", "t", "agent")
    return build_options(spec, approver, trace), trace


def test_harness_is_locked_down(tmp_path):
    options, _ = make_options(tmp_path, AutoApprover())
    assert options.tools == []                 # no built-in Claude Code tools
    assert options.setting_sources == []       # no local settings or CLAUDE.md
    assert isinstance(options.system_prompt, str)
    # Physical tools must never be pre-approved, or they would skip the approver.
    assert not set(options.allowed_tools) & {mcp_name(t) for t in PHYSICAL_TOOLS}
    assert set(options.allowed_tools) == {mcp_name(t) for t in READ_ONLY_TOOLS}


async def test_tool_lists_match_the_server(tmp_path):
    from mcp import Client
    async with Client(build_server(LabConfig(run_dir=tmp_path, run_id="t", enforce=True))) as client:
        served = {t.name for t in (await client.list_tools()).tools}
    assert served == set(READ_ONLY_TOOLS) | set(PHYSICAL_TOOLS)


def test_server_gets_the_enforcement_flag(tmp_path):
    on, _ = make_options(tmp_path / "a", AutoApprover(), "driver_only")
    off, _ = make_options(tmp_path / "b", AutoApprover(), "prompt_only")
    assert "--enforce" in on.mcp_servers["lab"]["args"]
    assert "--no-enforce" in off.mcp_servers["lab"]["args"]
    assert (tmp_path / "a" / "bench.json").exists()


async def test_can_use_tool_routes_to_approver_and_traces(tmp_path):
    approver = FakeApprover(approve=False)
    options, trace = make_options(tmp_path, approver)
    result = await options.can_use_tool(mcp_name("aspirate"), {"container": "reagents:A1", "volume_ul": 50}, None)
    trace.close()
    assert isinstance(result, PermissionResultDeny)
    assert approver.seen == [mcp_name("aspirate")]
    event = read_trace(tmp_path / "agent.jsonl")[-1]
    assert event["event"] == "approval" and event["approved"] is False


async def test_auto_approver_allows(tmp_path):
    options, _ = make_options(tmp_path, AutoApprover())
    result = await options.can_use_tool(mcp_name("dispense"), {}, None)
    assert isinstance(result, PermissionResultAllow)


async def test_cli_approver(monkeypatch):
    answers = iter(["y", "", "nope"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    approver = CliApprover()
    assert (await approver.decide("x", {})).approved
    assert not (await approver.decide("x", {})).approved   # default is deny
    assert not (await approver.decide("x", {})).approved


def test_isolated_from_other_mcp_servers(tmp_path):
    options, _ = make_options(tmp_path, AutoApprover())
    assert options.strict_mcp_config is True


async def test_server_starts_from_relative_run_dir_as_the_sdk_launches_it(tmp_path, monkeypatch):
    # Regression: the first live run passed relative paths while the agent process ran
    # with cwd=run_dir, so the server couldn't find bench.json and every lab tool failed.
    from pathlib import Path
    from mcp import Client, StdioServerParameters

    monkeypatch.chdir(tmp_path)
    spec = RunSpec(run_id="rel", run_dir=Path("runs/rel"), prompt="p", layers=CONFIGS["driver_only"],
                   bench=load_bench_spec(), model="claude-haiku-5-5")
    spec.run_dir.mkdir(parents=True)
    trace = TraceWriter(spec.run_dir / "agent.jsonl", "rel", "agent")
    options = build_options(spec, AutoApprover(), trace)
    server = options.mcp_servers["lab"]
    assert Path(options.cwd).is_absolute()
    assert Path(server["args"][server["args"].index("--run-dir") + 1]).is_absolute()

    params = StdioServerParameters(command=server["command"], args=server["args"], cwd=options.cwd)
    async with Client(params) as client:
        result = await client.call_tool("list_devices", {})
    assert not result.is_error, result.content
    assert (tmp_path / "runs/rel/lab.jsonl").exists()
    assert not (tmp_path / "runs/rel/runs").exists()  # the nested-directory symptom


async def test_cli_approver_denies_on_closed_stdin(monkeypatch):
    def eof(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    decision = await CliApprover().decide("x", {})
    assert not decision.approved and "stdin closed" in decision.reason


async def test_cli_approver_asks_one_question_at_a_time(monkeypatch):
    import asyncio
    import threading
    import time

    active, overlaps = [0], []
    lock = threading.Lock()

    def slow_input(_):
        with lock:
            active[0] += 1
            overlaps.append(active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return "y"

    monkeypatch.setattr("builtins.input", slow_input)
    approver = CliApprover()
    decisions = await asyncio.gather(*(approver.decide(f"t{i}", {}) for i in range(3)))
    assert all(d.approved for d in decisions)
    assert max(overlaps) == 1


async def test_cli_approver_discards_typeahead_and_records_answer(monkeypatch):
    import lab_agent_safety.agent.approval as approval

    calls = []
    monkeypatch.setattr(approval, "_discard_typeahead", lambda: calls.append("flush"))
    monkeypatch.setattr("builtins.input", lambda _: calls.append("ask") or " Y ")
    decision = await approval.CliApprover().decide("x", {})
    assert calls == ["flush", "ask"]  # buffered keys are dropped before the question is shown
    assert decision.approved and decision.answer == " Y "
