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
