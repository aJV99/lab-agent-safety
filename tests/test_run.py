"""The grid runner and its results cache, with a fake agent (no API calls)."""

import json
import shutil

import pytest

import lab_agent_safety
from lab_agent_safety.benchmark.run import cache_key, code_version, make_grid, run_grid
from lab_agent_safety.benchmark.tasks import load_tasks
from lab_agent_safety.config import CONFIGS

TASKS = load_tasks()
TWO_TASKS = [TASKS["pbs_fill"], TASKS["azide_preservative"]]
TWO_CONFIGS = [CONFIGS["prompt_only"], CONFIGS["driver_only"]]


def quiet(_):
    pass


def test_grid_is_rep_major():
    cells = make_grid(TWO_TASKS, TWO_CONFIGS, "m", reps=2)
    assert len(cells) == 8
    assert [c.rep for c in cells] == [0, 0, 0, 0, 1, 1, 1, 1]


def test_cache_key_changes_with_every_input():
    code = code_version()
    base = make_grid(TWO_TASKS[:1], TWO_CONFIGS[:1], "m", 1)[0]
    variants = [
        base,
        make_grid(TWO_TASKS[:1], TWO_CONFIGS[1:], "m", 1)[0],                      # config
        make_grid(TWO_TASKS[:1], TWO_CONFIGS[:1], "other-model", 1)[0],            # model
        make_grid(TWO_TASKS[:1], TWO_CONFIGS[:1], "m", 2)[1],                      # rep
        make_grid([TWO_TASKS[0].model_copy(update={"prompt": "x"})], TWO_CONFIGS[:1], "m", 1)[0],  # task
    ]
    keys = [cache_key(c, code) for c in variants]
    assert len(set(keys)) == len(keys)
    assert cache_key(base, code) == cache_key(base, code)
    assert cache_key(base, code) != cache_key(base, code + "x")


def test_code_version_tracks_run_code_but_not_the_grader(tmp_path, monkeypatch):
    pkg = tmp_path / "lab_agent_safety"
    shutil.copytree(lab_agent_safety.__path__[0], pkg, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(lab_agent_safety, "__file__", str(pkg / "__init__.py"))
    v1 = code_version()
    (pkg / "benchmark" / "grader.py").write_text("# changed\n")
    (pkg / "cli.py").write_text("# changed\n")
    assert code_version() == v1  # grading and the CLI don't change what an agent run does
    (pkg / "lab" / "devices" / "liquid_handler.yaml").write_text("changed: true\n")
    assert code_version() != v1  # a device rule does


async def test_second_run_is_all_cache_hits(tmp_path, FakeAgent):
    cells = make_grid(TWO_TASKS, TWO_CONFIGS, "m", reps=1)
    agent = FakeAgent()
    first = await run_grid(cells, tmp_path, parallel=2, runner=agent, log=quiet)
    assert [r.status for r in first] == ["ran"] * 4 and len(agent.calls) == 4
    for r in first:
        record = json.loads((r.run_dir / "run.json").read_text())
        assert record["summary"]["cost_usd"] == 0.01 and (r.run_dir / "lab.jsonl").exists()
    assert not list(tmp_path.glob("*.partial"))

    second = await run_grid(cells, tmp_path, parallel=2, runner=agent, log=quiet)
    assert [r.status for r in second] == ["cached"] * 4 and len(agent.calls) == 4  # no new calls
    assert [r.run_dir for r in second] == [r.run_dir for r in first]


async def test_failed_run_is_not_cached_and_runs_again(tmp_path, FakeAgent):
    cells = make_grid(TWO_TASKS[:1], TWO_CONFIGS[:1], "m", reps=1)
    results = await run_grid(cells, tmp_path, runner=FakeAgent(fail_on={"pbs_fill"}), log=quiet)
    assert results[0].status == "failed" and "harness broke" in results[0].error
    assert results[0].run_dir.name.endswith(".partial")  # left for debugging
    retry = await run_grid(cells, tmp_path, runner=FakeAgent(), log=quiet)
    assert retry[0].status == "ran" and not list(tmp_path.glob("*.partial"))


async def test_budget_stops_launching_new_runs(tmp_path, FakeAgent):
    cells = make_grid(TWO_TASKS, TWO_CONFIGS, "m", reps=1)
    agent = FakeAgent(cost=1.0)
    results = await run_grid(cells, tmp_path, parallel=1, budget_usd=1.5, runner=agent, log=quiet)
    assert [r.status for r in results] == ["ran", "ran", "skipped", "skipped"]


def test_cli_rejects_unknown_task(capsys):
    from lab_agent_safety.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["run", "--tasks", "no_such_task"])
    assert "unknown task" in str(exc.value)
