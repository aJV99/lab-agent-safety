"""Command line entry point: `lab-agent-safety <command>`.

    serve   run the lab MCP server over stdio (normally the agent harness launches it)
    chat    run the agent once on a prompt, with a person approving physical actions
"""

import argparse
import asyncio
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

    args, rest = parser.parse_known_args(argv)
    if args.command == "serve":
        from lab_agent_safety.mcp_server.server import main as serve_main
        serve_main(rest)
    elif rest:
        parser.error(f"unrecognised arguments: {' '.join(rest)}")
    else:
        _chat(args)
