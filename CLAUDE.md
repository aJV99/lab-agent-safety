# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`lab-agent-safety` is a benchmark that measures which **system-level safety layers** stop an AI agent from doing dangerous things when it controls lab robots. A Claude agent operates a simulated PyLabRobot lab through our own MCP server. About 30 tasks run repeatedly with each safety layer switched on or off, and the output is a results table showing what each layer buys you.

Related work (credit these in the README and state the difference): OpenLabAI and plr-mcp build MCP servers and safety controls for lab robots but don't benchmark agent safety behaviour. LabShield, SafeLab and ResponsibleRobotBench test whether *models* reason safely. This project measures how the *system around the model* should be designed.

Status: M1 in progress. The lab, wrapper, job queue, tracing, MCP server, agent harness, the 10 tasks, the grader, the grid runner and the report are built. The full 5-rep grid has not been run yet. `docs/PROGRESS.md` is the source of truth for what is built, and `docs/DECISIONS.md` for why.

## Commands

The project uses `uv`. Run everything through `uv run` so the project venv is used.

```bash
uv sync                          # install / update deps from uv.lock
uv add <pkg>                     # add a runtime dependency
uv add --dev <pkg>               # add a dev dependency (pytest etc.)
uv run pytest                    # full suite (offline; no API calls)
uv run pytest tests/test_safety.py::test_every_command_is_logged   # single test
uv run lab-agent-safety chat "<prompt>" --config prompt_only --approver cli   # one live agent run (needs ANTHROPIC_API_KEY, from the shell or .env; see .env.example)
uv run lab-agent-safety serve --run-dir runs/x --run-id x --enforce         # MCP server over stdio
uv run lab-agent-safety run --reps 5 --budget-usd 5                         # benchmark grid (paid; cached runs are skipped)
uv run lab-agent-safety report --reps 5 --out results/m1.md --json results/m1.json   # grade cached runs into the results table (free)
```

Run the tests after every meaningful change and report failures with their real output.

## Architecture (planned, M1 → M2)

- **Simulated lab**: PyLabRobot simulated backends (liquid handler, plus a plate reader if PLR supports one; otherwise a thin readings model: absorbance from concentration plus noise). Each device has a YAML reference file holding its capabilities, safety limits (e.g. max volumes) and interlocks (e.g. no dispensing without a tip).
- **Safety wrapper around PyLabRobot**: the single choke point for all device commands. It also hosts fault injection (failed tip pickup, timeout, device dropout). No Docker.
- **Four switchable safety layers** (config-driven): (a) prompt rules in the system prompt, (b) driver enforcement in the wrapper, (c) reviewer sub-agent: a second Claude checks the plan against the reference files before execution, (d) dry run: the plan runs on a shadow copy of the simulator first. Configurations benchmarked: none / prompt / driver / prompt+driver / +reviewer / +dry run.
- **Execution**: ordered job queue with per-device locks. Each job reports success or failure.
- **Agent**: Claude Agent SDK (Python) talking to our MCP server (MCP Python SDK). Tools: list devices, describe device (loads only that device's reference file), aspirate/dispense, read plate, run sequence, dry run, job status. Approval is pluggable: CLI human, or a policy approver for unattended evals. One agent skill (SKILL.md): serial dilution plus plate read on this bench.
- **Tracing**: every run writes a JSONL trace of tool calls, approvals, violations and tokens.
- **Benchmark**: tasks grouped as normal / unclear (should ask first) / dangerous (should refuse or be blocked) / fault-injected (should stop safely, no retry). About 5 repeats per task, reporting pass rate and variance. Full grid on `claude-haiku-5-5`, key configs on `claude-sonnet-5-5`. Cost controls: prompt caching of the shared system prompt and reference files, a results cache, and a small CI subset.

## Invariants: do not break these

1. **The wrapper always detects violations, even when enforcement is off.** With enforcement off it logs the violation and lets the command through. That keeps "unsafe actions attempted" measured the same way in every configuration. Enforcement on/off changes only whether the command is blocked, never whether it is detected.
2. **No automatic retries on physical actions.** A failed physical action usually fails again, and a retry can be unsafe. Faults must lead to a safe stop.
3. **Grading is deterministic, from simulator end state plus the trace.** Never trust the model's own claim that it succeeded.
4. Each safety layer must be independently switchable by config so the ablation stays clean.

## How to work here

- **Scope**: work on milestone M1 only until the user says otherwise. M1 covers the sim lab, reference files, the wrapper, pytest tests for limits and interlocks, the MCP server, the agent with CLI approval, tracing, and about 10 tasks run as "prompt only" vs "driver enforcement" to produce a first results table.
- **Check current docs before coding** against PyLabRobot, the MCP Python SDK or the Claude Agent SDK. Don't rely on memory for their APIs. Use the `claude-api` skill for anything touching the Claude API or the Agent SDK.
- **Ask before any choice that shapes the architecture.** Keep code plain and readable: the user must be able to defend every line in a technical deep dive. Explain significant design decisions briefly as you go.
- **Keep `docs/DECISIONS.md`** up to date: each decision, the options considered, and why.
- **Keep `docs/PROGRESS.md`** up to date: milestone status, what actually works, real benchmark numbers once they exist, and a "verified facts" list containing only things that are built and tested. An external assistant reads this file, so it must never overstate.
- **Public repo**: MIT licence unless PyLabRobot's licence suggests otherwise. Never claim or imply that this implements any unpublished or proprietary hardware or safety standard. Keep committed files about the benchmark itself, with no personal or career context.
- **Commits and PRs** only when asked, authored as the user, with no Claude attribution anywhere.
