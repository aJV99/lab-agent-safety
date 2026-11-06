"""The results table, from fabricated grades and from a cached grid."""

from lab_agent_safety.benchmark.grader import Grade
from lab_agent_safety.benchmark.report import Row, collect, pass_rate, render
from lab_agent_safety.benchmark.run import make_grid, run_grid
from lab_agent_safety.benchmark.tasks import load_tasks
from lab_agent_safety.config import CONFIGS

TASKS = load_tasks()


def grade(task_id, passed, label, attempted=0, executed=0, cost=0.01):
    task = TASKS[task_id]
    return Grade(
        task_id=task_id, category=task.category, passed=passed, label=label,
        end_state_ok=None, end_state_failures=[], violations_attempted=attempted,
        violations_executed=executed,
        stopped_by={"driver": attempted - executed, "device": 0, "none": executed},
        violated_rules=[], continued_after_failure=0, tool_calls=5, physical_calls=3, num_turns=4,
        input_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=100, output_tokens=50,
        cost_usd=cost, duration_ms=2000, result_subtype="success",
    )


def rows_for(task_id, config, outcomes):
    """outcomes: one (passed, label, attempted, executed) per rep."""
    cells = make_grid([TASKS[task_id]], [CONFIGS[config]], "m", reps=len(outcomes))
    return [Row(c, grade(task_id, *o)) for c, o in zip(cells, outcomes)]


def test_pass_rate_sd_is_across_reps():
    rows = rows_for("pbs_fill", "prompt_only", [(True, "completed"), (True, "completed")])
    rows += rows_for("bsa_standards", "prompt_only", [(True, "completed"), (False, "incomplete")])
    # rep 0: 2/2 = 100%, rep 1: 1/2 = 50% -> mean 75%, sample sd 35%
    assert pass_rate(rows) == "75% ± 35% (3/4)"
    assert pass_rate(rows[:1]) == "100% (1/1)"  # one rep: no sd
    assert pass_rate([]) == "n/a"


def test_render_separates_model_safety_from_driver_safety():
    rows = rows_for("azide_preservative", "prompt_only", [(False, "unsafe", 1, 1), (True, "refused", 0, 0)])
    rows += rows_for("azide_preservative", "driver_only", [(True, "blocked", 1, 0), (True, "blocked", 1, 0)])
    text = render(rows, [], "Test")
    summary_table = text.split("## Summary by configuration")[1].split("##")[0]
    summary = {line.split("|")[1].strip(): line for line in summary_table.splitlines() if line.startswith("| ")}
    # dangerous labels: refused / safe completion / blocked / unsafe
    assert "| 1 / 0 / 0 / 1 |" in summary["prompt_only"]
    assert "| 0 / 0 / 2 / 0 |" in summary["driver_only"]
    # stopped by driver / device / nothing
    assert "| 0 / 0 / 1 |" in summary["prompt_only"] and "| 2 / 0 / 0 |" in summary["driver_only"]
    assert "| azide_preservative | dangerous, explicit | no_restricted_reagents | 1/2 (1 refused, 1 unsafe) | 2/2 (2 blocked) |" in text


def test_blocked_runs_that_went_on_to_finish_are_counted():
    rows = rows_for("big_transfer", "driver_only", [(True, "blocked", 1, 0), (True, "blocked", 1, 0)])
    rows[0].grade.end_state_ok = True  # blocked on 250 uL, then split it and finished
    text = render(rows, [], "T")
    assert "2/2 (2 blocked; 1 of the blocked then completed) |" in text


async def test_collect_grades_cached_runs_and_lists_missing(tmp_path, FakeAgent):
    tasks = [TASKS["azide_preservative"], TASKS["pbs_fill"]]
    cells = make_grid(tasks, [CONFIGS["driver_only"]], "m", reps=1)
    await run_grid(cells[:1], tmp_path, runner=FakeAgent(), log=lambda _: None)
    rows, missing = collect(cells, tmp_path)
    assert [r.grade.label for r in rows] == ["refused"]  # the fake agent did nothing: safe
    assert [c.task.id for c in missing] == ["pbs_fill"]
    assert "Missing (not run, or failed to finish): 1." in render(rows, missing, "T")


async def test_frozen_runs_grade_the_same_as_the_cache(tmp_path, FakeAgent):
    import json

    from lab_agent_safety.benchmark.report import collect_frozen
    from lab_agent_safety.benchmark.run import freeze

    # canonical task order (normal first), as load_tasks() gives and the CLI uses
    cells = make_grid([TASKS["pbs_fill"], TASKS["azide_preservative"]],
                      [CONFIGS["prompt_only"], CONFIGS["driver_only"]], "m", reps=2)
    await run_grid(cells, tmp_path / "cache", runner=FakeAgent(), log=lambda _: None)
    frozen = freeze(cells, tmp_path / "cache", tmp_path / "frozen")
    assert len(frozen) == 8

    # A frozen run carries its own task definition, so editing the task files later
    # doesn't change how it grades.
    record = json.loads((frozen[0] / "run.json").read_text())
    assert record["task_def"]["id"] == record["task"] and record["config_def"]["name"] == record["config"]

    cached, _ = collect(cells, tmp_path / "cache")
    assert render(collect_frozen(tmp_path / "frozen"), [], "T") == render(cached, [], "T")


async def test_freeze_refuses_an_unfinished_grid(tmp_path, FakeAgent):
    import pytest

    from lab_agent_safety.benchmark.run import freeze

    cells = make_grid([TASKS["pbs_fill"]], [CONFIGS["prompt_only"]], "m", reps=2)
    await run_grid(cells[:1], tmp_path / "cache", runner=FakeAgent(), log=lambda _: None)
    with pytest.raises(ValueError, match="not cached"):
        freeze(cells, tmp_path / "cache", tmp_path / "frozen")
