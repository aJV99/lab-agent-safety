"""Command line entry point: `lab-agent-safety <command>`.

    serve   run the lab MCP server over stdio (normally the agent harness launches it)
    chat    run the agent once on a prompt, with a person approving physical actions
    run     run the benchmark grid (tasks x configs x reps), skipping cached runs
    report  grade the cached runs of a grid (or a frozen folder) and print or write the results table
    freeze  copy a finished grid out of the cache into a folder, to publish its traces
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from lab_agent_safety.agent.approval import AutoApprover, CliApprover
from lab_agent_safety.agent.runner import RunSpec, run_agent
from lab_agent_safety.config import CONFIGS, DEFAULT_MODEL
from lab_agent_safety.lab.bench import load_bench_spec


def load_env() -> str:
    """Load a .env file into os.environ, if one is found.

    Searches upward from the current directory (not from this file, which may be
    installed elsewhere). Variables already set in the shell win over the file. The
    Agent SDK passes os.environ on to the Claude Code process, so ANTHROPIC_API_KEY
    set here reaches it. Returns the path loaded, or "" if none was found.
    """
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)
    return path


def _chat(args: argparse.Namespace) -> None:
    if args.approver == "cli" and not CliApprover.available():
        sys.exit(
            "--approver cli needs an interactive terminal (stdin is not a TTY). "
            "Run this in your own terminal, or use --approver auto."
        )
    run_id = args.run_id or f"chat-{time.strftime('%Y%m%d-%H%M%S')}"
    spec = RunSpec(
        run_id=run_id,
        run_dir=Path(args.runs_dir) / run_id,
        prompt=args.prompt,
        layers=CONFIGS[args.config],
        bench=load_bench_spec(),
        model=args.model,
    )
    approver = AutoApprover() if args.approver == "auto" else CliApprover()
    summary = asyncio.run(run_agent(spec, approver))
    print(f"\n--- {summary.subtype} in {summary.num_turns} turns, {summary.duration_ms} ms, "
          f"${summary.cost_usd or 0:.4f}")
    print(summary.final_text)
    print(f"Trace and end state: {spec.run_dir}")


def _grid(args: argparse.Namespace):
    from lab_agent_safety.benchmark.run import make_grid
    from lab_agent_safety.benchmark.tasks import load_tasks

    tasks = load_tasks()
    task_ids = list(tasks) if args.tasks == "all" else args.tasks.split(",")
    config_names = args.configs.split(",")
    for name in task_ids:
        if name not in tasks:
            sys.exit(f"unknown task {name!r}; known: {', '.join(tasks)}")
    for name in config_names:
        if name not in CONFIGS:
            sys.exit(f"unknown config {name!r}; known: {', '.join(CONFIGS)}")
    return make_grid([tasks[t] for t in task_ids], [CONFIGS[c] for c in config_names], args.model, args.reps)


def _run(args: argparse.Namespace) -> None:
    from lab_agent_safety.benchmark.run import run_grid

    cells = _grid(args)
    results = asyncio.run(run_grid(cells, Path(args.cache_dir), args.parallel, args.budget_usd))
    counts = {s: sum(r.status == s for r in results) for s in ("cached", "ran", "failed", "skipped")}
    spent = sum(r.cost_usd for r in results if r.status == "ran")
    print(f"\n{len(cells)} runs: {counts['ran']} ran (${spent:.4f}), {counts['cached']} cached, "
          f"{counts['failed']} failed, {counts['skipped']} skipped (budget)")
    if counts["failed"]:
        sys.exit(1)


def _report(args: argparse.Namespace) -> None:
    from lab_agent_safety.benchmark.report import collect, collect_frozen, render

    if args.runs_dir:  # a frozen folder: grade what is there, ignoring the grid arguments
        rows, missing = collect_frozen(Path(args.runs_dir)), []
    else:
        rows, missing = collect(_grid(args), Path(args.cache_dir))
    text = render(rows, missing, args.title)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    if args.json:
        records = [{"task": r.cell.task.id, "config": r.cell.config.name, "model": r.cell.model,
                    "rep": r.cell.rep, **r.grade.to_dict()} for r in rows]
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(records, indent=2))
        print(f"wrote {args.json}")


def _freeze(args: argparse.Namespace) -> None:
    from lab_agent_safety.benchmark.run import freeze

    try:
        frozen = freeze(_grid(args), Path(args.cache_dir), Path(args.to))
    except ValueError as e:
        sys.exit(f"freeze: {e}")
    print(f"froze {len(frozen)} runs into {args.to}")


def _add_grid_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--tasks", default="all", help="comma-separated task ids, or 'all'")
    p.add_argument("--configs", default="prompt_only,driver_only", help="comma-separated config names")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--cache-dir", default="results/cache")


def main(argv: list[str] | None = None) -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="lab-agent-safety")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the lab MCP server over stdio", add_help=False)

    chat = sub.add_parser("chat", help="run the agent once on a prompt")
    chat.add_argument("prompt")
    chat.add_argument("--config", choices=sorted(CONFIGS), default="prompt_driver")
    chat.add_argument("--model", default=DEFAULT_MODEL)
    chat.add_argument("--approver", choices=["cli", "auto"], default="cli")
    chat.add_argument("--runs-dir", default="runs")
    chat.add_argument("--run-id")

    run = sub.add_parser("run", help="run the benchmark grid, skipping cached runs")
    _add_grid_args(run)
    run.add_argument("--parallel", type=int, default=4, help="runs in flight at once")
    run.add_argument("--budget-usd", type=float, default=5.0,
                     help="stop launching runs once this much has been spent (in-flight runs finish)")

    report = sub.add_parser("report", help="grade the cached runs of a grid and print the results table")
    _add_grid_args(report)
    report.add_argument("--title", default="Results")
    report.add_argument("--out", help="write the markdown table here instead of printing it")
    report.add_argument("--json", help="also write every run's grade as JSON here")
    report.add_argument("--runs-dir", help="grade a frozen folder of runs instead of the cache")

    freeze = sub.add_parser("freeze", help="copy a finished grid out of the cache, to publish its traces")
    _add_grid_args(freeze)
    freeze.add_argument("--to", required=True, help="destination folder, e.g. results/m1/runs")

    args, rest = parser.parse_known_args(argv)
    if args.command == "serve":
        from lab_agent_safety.mcp_server.server import main as serve_main
        serve_main(rest)
    elif rest:
        parser.error(f"unrecognised arguments: {' '.join(rest)}")
    elif args.command == "chat":
        _chat(args)
    elif args.command == "run":
        _run(args)
    elif args.command == "freeze":
        _freeze(args)
    else:
        _report(args)
