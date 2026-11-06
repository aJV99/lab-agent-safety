"""The results table: grade every cached run in a grid and summarise it as markdown.

Grading happens here, from the stored traces (grader.py), never at run time. So a
grader fix only needs `report` to be run again, not the agents.

Pass rates are shown as "mean ± sd (passes/runs)". The sd is taken across reps: for
each rep, the pass rate over that rep's runs, then the sample standard deviation of
those per-rep rates. It shows how much the headline number moves from one full
repetition of the task set to the next. With fewer than two reps there is no sd.
"""

import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lab_agent_safety.benchmark.grader import Grade, grade_run
from lab_agent_safety.benchmark.run import Cell, code_version, is_cached, run_dir_for

DANGEROUS_LABELS = ("refused", "safe_completion", "blocked", "unsafe")


@dataclass
class Row:
    cell: Cell
    grade: Grade


def collect(cells: list[Cell], cache_dir: Path) -> tuple[list[Row], list[Cell]]:
    """Grades for every cell that has a cached run, plus the cells that don't."""
    code = code_version()
    rows, missing = [], []
    for cell in cells:
        run_dir = run_dir_for(cell, code, cache_dir)
        if is_cached(run_dir):
            rows.append(Row(cell, grade_run(cell.task, run_dir)))
        else:
            missing.append(cell)
    return rows, missing


# ---- numbers -----------------------------------------------------------------------------


def pass_rate(rows: list[Row]) -> str:
    if not rows:
        return "n/a"
    passes = sum(r.grade.passed for r in rows)
    mean = passes / len(rows)
    by_rep: dict[int, list[bool]] = {}
    for r in rows:
        by_rep.setdefault(r.cell.rep, []).append(r.grade.passed)
    rates = [sum(v) / len(v) for v in by_rep.values()]
    sd = f" ± {statistics.stdev(rates):.0%}" if len(rates) >= 2 else ""
    return f"{mean:.0%}{sd} ({passes}/{len(rows)})"


def mean(values: list[float | int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.fmean(present) if present else None


def fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


# ---- tables ------------------------------------------------------------------------------


def _table(header: list[str], body: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return lines


def render(rows: list[Row], missing: list[Cell], title: str) -> str:
    configs = list(dict.fromkeys(r.cell.config.name for r in rows))
    tasks = list(dict.fromkeys(r.cell.task.id for r in rows))
    by_config = {c: [r for r in rows if r.cell.config.name == c] for c in configs}
    models = sorted({r.cell.model for r in rows})
    reps = sorted({r.cell.rep for r in rows})

    out = [f"# {title}", ""]
    out.append(f"Model: {', '.join(models) or 'n/a'}. Runs graded: {len(rows)}"
               f" ({len(tasks)} tasks, {len(configs)} configs, {len(reps)} reps)."
               f" Missing (not run, or failed to finish): {len(missing)}.")
    out.append("")

    # 1. headline
    out.append("## Summary by configuration")
    out.append("")
    body = []
    for c, rs in by_config.items():
        normal = [r for r in rs if r.cell.task.category == "normal"]
        dangerous = [r for r in rs if r.cell.task.category == "dangerous"]
        labels = Counter(r.grade.label for r in dangerous)
        stops = Counter()
        for r in rs:
            stops.update(r.grade.stopped_by)
        body.append([
            c,
            pass_rate(normal),
            pass_rate(dangerous),
            " / ".join(str(labels[label]) for label in DANGEROUS_LABELS),
            f"{sum(r.grade.violations_attempted > 0 for r in rs)}/{len(rs)}",
            f"{stops['driver']} / {stops['device']} / {stops['none']}",
            f"{sum(r.grade.continued_after_failure > 0 for r in rs)}/{len(rs)}",
            f"{sum(r.grade.read_rules_first for r in rs)}/{len(rs)}",
            fmt(mean([r.grade.tool_calls for r in rs]), ".1f"),
            fmt(mean([r.grade.cost_usd for r in rs]), ".4f"),
            fmt(mean([r.grade.duration_ms / 1000 if r.grade.duration_ms else None for r in rs]), ".0f"),
        ])
    out += _table(
        ["Config", "Normal tasks passed", "Dangerous tasks kept safe",
         "Dangerous: refused / safe completion / blocked / unsafe",
         "Runs with a violation attempted", "Violating commands stopped by driver / device / nothing",
         "Runs that continued a batch after a failure", "Runs that read the device rules before acting",
         "Mean tool calls", "Mean cost (USD)", "Mean duration (s)"],
        body,
    )
    out.append("")

    # 2. dangerous tasks by kind
    out.append("## Dangerous tasks by kind")
    out.append("")
    out.append("Explicit: the prompt asks for the unsafe action. Implicit: the obvious way to do a reasonable request breaks a rule.")
    out.append("")
    body = []
    for kind in ("explicit", "implicit"):
        body.append([kind] + [pass_rate([r for r in by_config[c] if r.cell.task.kind == kind]) for c in configs])
    out += _table(["Kind"] + configs, body)
    out.append("")

    # 3. per task
    out.append("## By task")
    out.append("")
    out.append("Each cell: runs passed, then how the runs ended. \"Blocked then completed\": the driver refused a command, and the agent went on to do the task within the rules.")
    out.append("")
    body = []
    for t in tasks:
        task = next(r.cell.task for r in rows if r.cell.task.id == t)
        cells = []
        for c in configs:
            rs = [r for r in by_config[c] if r.cell.task.id == t]
            labels = Counter(r.grade.label for r in rs)
            ended = ", ".join(f"{n} {label}" for label, n in sorted(labels.items()))
            recovered = sum(r.grade.label == "blocked" and bool(r.grade.end_state_ok) for r in rs)
            if recovered:  # blocked, then finished the task within the rules
                ended += f"; {recovered} of the blocked then completed"
            cells.append(f"{sum(r.grade.passed for r in rs)}/{len(rs)} ({ended})" if rs else "n/a")
        kind = task.category if task.category == "normal" else f"dangerous, {task.kind}"
        target = ", ".join(task.target_rules) or "-"
        body.append([t, kind, target] + cells)
    out += _table(["Task", "Category", "Target rule"] + configs, body)
    out.append("")

    # 4. cost
    total = sum(r.grade.cost_usd or 0 for r in rows)
    out.append("## Cost")
    out.append("")
    body = []
    for c, rs in by_config.items():
        body.append([
            c,
            fmt(sum(r.grade.cost_usd or 0 for r in rs), ".4f"),
            fmt(mean([r.grade.num_turns for r in rs]), ".1f"),
            fmt(mean([r.grade.output_tokens for r in rs]), ".0f"),
            fmt(mean([r.grade.cache_read_input_tokens for r in rs]), ".0f"),
        ])
    out += _table(["Config", "Total cost (USD)", "Mean turns", "Mean output tokens", "Mean cached input tokens"], body)
    out.append("")
    out.append(f"Total cost of the graded runs: ${total:.4f}.")
    out.append("")
    ended_early = Counter(r.grade.result_subtype for r in rows if r.grade.result_subtype != "success")
    if ended_early:
        out.append("Runs that ended other than with `success`: "
                   + ", ".join(f"{n} {s}" for s, n in sorted(ended_early.items(), key=str)) + ".")
        out.append("")
    out.append("Pass rates are mean ± sample standard deviation across reps (passes/runs).")
    return "\n".join(out) + "\n"
