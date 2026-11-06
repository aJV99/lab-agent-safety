"""Run the benchmark grid: tasks x configs x reps, with a results cache (D19).

Each run's directory *is* its cache entry:
    results/cache/<task>__<config>__<model>__r<rep>__<key12>/
        bench.json  agent.jsonl  lab.jsonl  snapshot.json  run.json
A run is cached only when it finishes, meaning the agent returned a result of any kind,
including running out of turns. It runs in a `.partial` directory that is renamed
when it completes. A run that crashes leaves its `.partial` for debugging and is run
again next time.

The cache key covers everything that can change what happens during a run: the task,
the layer config, the model, the rep (which is also the simulator seed), the installed
PyLabRobot / MCP / Agent SDK versions, and a hash of the source code that runs during
an agent run (lab, MCP server, agent harness). The grader and the report are
deliberately left out: grading is recomputed from the stored traces, so fixing the
grader never needs a paid re-run.
"""

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import lab_agent_safety
from lab_agent_safety.agent.approval import Approver, AutoApprover
from lab_agent_safety.agent.runner import RunSpec, RunSummary, run_agent
from lab_agent_safety.benchmark.tasks import Task
from lab_agent_safety.config import LayerConfig
from lab_agent_safety.lab.bench import load_bench_spec

DEFAULT_CACHE_DIR = Path("results/cache")
# Source that runs during an agent run, relative to the package. A change here
# invalidates cached runs; a change anywhere else (grader, report, CLI, docs) doesn't.
RUN_SOURCES = ("lab", "mcp_server", "agent", "config.py", "tracing.py")
PACKAGES = ("pylabrobot", "mcp", "claude-agent-sdk")

Runner = Callable[[RunSpec, Approver], Awaitable[RunSummary]]


def code_version() -> str:
    root = Path(lab_agent_safety.__file__).parent
    h = sha256()
    for entry in RUN_SOURCES:
        path = root / entry
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for f in files:
            if f.is_file() and f.suffix in (".py", ".yaml"):
                h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    for pkg in PACKAGES:
        h.update(f"{pkg}=={version(pkg)}\0".encode())
    return h.hexdigest()


@dataclass(frozen=True)
class Cell:
    """One run in the grid."""

    task: Task
    config: LayerConfig
    model: str
    rep: int


def make_grid(tasks: list[Task], configs: list[LayerConfig], model: str, reps: int) -> list[Cell]:
    """Rep-major order: every task and config once before any second rep, so a stopped
    or budget-limited grid still covers the whole table evenly."""
    return [Cell(t, c, model, r) for r in range(reps) for t in tasks for c in configs]


def cache_key(cell: Cell, code: str) -> str:
    inputs = {
        "task": cell.task.model_dump(mode="json"),
        "config": cell.config.model_dump(),
        "model": cell.model,
        "rep": cell.rep,
        "code": code,
    }
    return sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def run_dir_for(cell: Cell, code: str, cache_dir: Path) -> Path:
    key = cache_key(cell, code)
    return cache_dir / f"{cell.task.id}__{cell.config.name}__{cell.model}__r{cell.rep}__{key[:12]}"


def is_cached(run_dir: Path) -> bool:
    return (run_dir / "run.json").exists()


@dataclass
class GridRun:
    cell: Cell
    run_dir: Path
    status: str  # cached | ran | failed | skipped (budget reached)
    cost_usd: float = 0.0
    error: str | None = None


async def run_grid(
    cells: list[Cell],
    cache_dir: Path = DEFAULT_CACHE_DIR,
    parallel: int = 4,
    budget_usd: float | None = None,
    runner: Runner = run_agent,
    log: Callable[[str], None] = print,
) -> list[GridRun]:
    """Run every cell that isn't cached yet.

    `budget_usd` stops *launching* runs once this many dollars have been spent in this
    call. Runs already in flight finish, so the overshoot is at most `parallel` runs,
    each capped by RunSpec.max_budget_usd. Every run uses the auto-approve policy (D3).
    """
    code = code_version()
    cache_dir.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(parallel)
    spent = 0.0

    async def one(cell: Cell) -> GridRun:
        nonlocal spent
        final = run_dir_for(cell, code, cache_dir)
        if is_cached(final):
            cost = json.loads((final / "run.json").read_text())["summary"]["cost_usd"] or 0.0
            return GridRun(cell, final, "cached", cost)
        async with gate:
            if budget_usd is not None and spent >= budget_usd:
                return GridRun(cell, final, "skipped")
            partial = final.with_name(final.name + ".partial")
            shutil.rmtree(partial, ignore_errors=True)
            spec = RunSpec(
                run_id=final.name,
                run_dir=partial,
                prompt=cell.task.prompt,
                layers=cell.config,
                bench=cell.task.bench_spec(load_bench_spec()),
                model=cell.model,
                seed=cell.rep,
            )
            try:
                summary = await runner(spec, AutoApprover())
            except Exception as e:  # infrastructure failure: not a data point, not cached
                log(f"FAILED  {final.name}: {type(e).__name__}: {e}")
                return GridRun(cell, partial, "failed", error=f"{type(e).__name__}: {e}")
            cost = summary.cost_usd or 0.0
            spent += cost
            record = {
                "task": cell.task.id, "config": cell.config.name, "model": cell.model, "rep": cell.rep,
                "key": cache_key(cell, code), "code_version": code, "summary": asdict(summary),
            }
            (partial / "run.json").write_text(json.dumps(record, indent=2))
            partial.rename(final)  # atomic: a run is either fully cached or not at all
            log(f"ran     {final.name}: {summary.subtype}, {summary.num_turns} turns, ${cost:.4f}")
            return GridRun(cell, final, "ran", cost)

    return list(await asyncio.gather(*(one(c) for c in cells)))
