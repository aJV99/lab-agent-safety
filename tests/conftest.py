import pytest

from lab_agent_safety.lab.bench import Bench, load_bench_spec


@pytest.fixture
async def bench():
    b = Bench(load_bench_spec(), seed=0)
    await b.setup()
    yield b
    await b.stop()


@pytest.fixture
def scripted_run(tmp_path):
    """Run a fixed list of commands through the real lab stack (queue -> wrapper -> bench),
    exactly as the MCP server would, and return the run directory.

    Used to check tasks and the grader without an agent: the traces and snapshot it
    leaves are the same files a live run leaves.
    """
    from lab_agent_safety.mcp_server.server import Lab, LabConfig

    async def run(task, commands, enforce: bool, name: str = "run"):
        run_dir = tmp_path / f"{task.id}-{name}-{'on' if enforce else 'off'}"
        run_dir.mkdir()
        bench_file = run_dir / "bench.json"
        bench_file.write_text(task.bench_spec(load_bench_spec()).model_dump_json())
        lab = Lab(LabConfig(run_dir=run_dir, run_id=run_dir.name, enforce=enforce, bench_path=bench_file))
        await lab.start()
        for cmd in commands:
            await lab.queue.submit(cmd)
            lab.write_snapshot()
        await lab.bench.stop()
        lab.trace.close()
        return run_dir

    return run


@pytest.fixture
def FakeAgent():
    """A stand-in for run_agent (no API calls): starts the real lab, does nothing, and
    records a result. The fixture returns the class so tests can configure it."""
    from lab_agent_safety.agent.runner import RunSummary
    from lab_agent_safety.mcp_server.server import Lab, LabConfig
    from lab_agent_safety.tracing import TraceWriter

    class FakeAgent:
        """Stands in for run_agent: starts the real lab, does nothing, records a result."""

        def __init__(self, cost=0.01, fail_on=()):
            self.calls = []
            self.cost = cost
            self.fail_on = set(fail_on)

        async def __call__(self, spec, approver):
            self.calls.append(spec.run_id)
            spec.run_dir.mkdir(parents=True, exist_ok=True)
            bench_file = spec.run_dir / "bench.json"
            bench_file.write_text(spec.bench.model_dump_json())
            lab = Lab(LabConfig(spec.run_dir, spec.run_id, spec.layers.driver_enforcement, bench_file, spec.seed))
            await lab.start()
            await lab.bench.stop()
            lab.trace.close()
            if spec.run_id.split("__")[0] in self.fail_on:
                raise RuntimeError("harness broke")
            with TraceWriter(spec.run_dir / "agent.jsonl", spec.run_id, "agent") as t:
                t.write("result", subtype="success", num_turns=1, duration_ms=10, total_cost_usd=self.cost, usage={})
            return RunSummary(spec.run_id, "success", "done", 1, 10, self.cost)

    return FakeAgent
