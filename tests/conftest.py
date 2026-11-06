import pytest

from lab_agent_safety.lab.bench import Bench, load_bench_spec


@pytest.fixture
async def bench():
    b = Bench(load_bench_spec(), seed=0)
    await b.setup()
    yield b
    await b.stop()
