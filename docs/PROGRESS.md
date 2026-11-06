# Progress

## Milestone status

| Milestone | Target | Status |
|---|---|---|
| M1: first real result | 16 Nov 2026 | In progress (step 10: runner and report built; full grid next) |
| M2: full benchmark | 30 Nov 2026 | Not started |
| M3: any MCP server, write-up | n/a | Not started |

### M1 checklist

- [x] Day-one checks (PLR licence, volume and tip tracking, plate reader simulation)
- [x] Project housekeeping (licence, README, decisions log, dependencies)
- [x] Device reference files and validated loader
- [x] Simulated bench, composition tracking, plate readings model
- [x] Safety wrapper (limits and interlocks, detect-always)
- [x] Job queue with device locks, no retries
- [x] JSONL tracing
- [x] MCP server (stdio, MCP Python SDK 2.3)
- [x] Agent harness with CLI and auto approval
- [x] First live agent runs with CLI approval (claude-haiku-5-5). They found and fixed a path bug (D15) and a type-ahead approval bug (D16)
- [x] 10 tasks (4 normal, 6 dangerous) and the deterministic grader, including the D17 batch-continuation metric (D18)
- [x] Grid runner with a results cache, and the results report (D20)
- [x] Pilot (rep 0 of the grid, 20 runs, $0.033). It found and fixed a PyLabRobot volume-tracking bug on failed transfers (D19)
- [ ] First results table: prompt only vs driver only, 5 reps

## What actually works

All of the following is covered by the test suite (140 tests, `uv run pytest`):

- A simulated bench on PyLabRobot 0.2.2: liquid handler, 300 uL tips, a 6-well reagent reservoir, a 96-well assay plate, and a plate reader, with PLR volume and tip tracking on.
- Two device reference files (YAML) validated by strict pydantic models. A misspelt or contradictory limit fails at load time.
- A composition sidecar that tracks analyte amounts through every transfer (well-mixed model).
- An absorbance readings model on top of PLR's simulated plate reader: linear in concentration, clipped at saturation, with seeded noise.
- A safety wrapper with 3 limits and 8 interlocks. Every violation scenario is tested to be detected identically with enforcement on and off. With it on, the command is blocked and the lab is unchanged. With it off, the command reaches the device.
- An ordered job queue with per-device locks and no retry path.
- JSONL tracing (two streams per run, merged by timestamp).
- An MCP server with 8 tools, tested both in-process and as a real stdio subprocess. With enforcement off, violations are logged but never shown to the agent.
- Agent harness wiring: no built-in tools, no local settings, strict MCP isolation, and physical tools always routed through the approver.
- 10 benchmark tasks in YAML (4 normal, 6 dangerous), validated on load. For every task, a compliant command script run through the real lab stack grades as a pass. For every dangerous task, the obvious unsafe script executes with enforcement off (physically possible) and is blocked with enforcement on.
- A deterministic grader that reads only `snapshot.json`, `lab.jsonl` and `agent.jsonl`, never the agent's messages. It gives pass or fail, a label, violations attempted and executed with who stopped them, the D17 batch-continuation count, tool calls, tokens, cost and latency. It has graded a 20-run live pilot. In one pilot run the agent made a 1:3 dilution while claiming 1:2, and the grader marked it incomplete from the end state.
- A grid runner (tasks x configs x reps) whose results cache is keyed on everything that can change a run, including a hash of the run-time source. Grading is recomputed from stored traces, so grader changes never need re-runs. Failed runs are not cached.
- A markdown results report: pass rate ± sd across reps, how dangerous runs ended, who stopped each violation, D17, tool calls, tokens, cost and latency.
- Failed aspirates and dispenses leave volumes unchanged. This works around a PyLabRobot 0.2.2 tracker bug that the pilot found (D19).
- A live run end to end (runs/chat-20261103-210006, not committed): a Haiku agent drove the simulated bench through the MCP server with a person approving each physical step. Prompt caching was confirmed (cached input grew from 2.6k to 9.2k tokens across turns). Physical calls sent together in one turn executed in the order issued. Cost: $0.0054.

## Benchmark numbers

None yet. Only a one-rep pilot has run so far, which is too few for pass rates. The full grid is next.

## Verified facts

Only things that are built and tested:

- Built an MCP server in Python with the MCP Python SDK that exposes a simulated lab robot (PyLabRobot) as 8 tools, tested in-process and over stdio.
- Built a safety wrapper around PyLabRobot that enforces device limits and interlocks declared in YAML reference files, with a tested invariant that violations are detected identically whether or not enforcement is on.
- Built an ordered job queue with per-device locks and no automatic retries for physical actions.
- Built JSONL run tracing across the agent harness and the MCP server.
- Built a deterministic grader that scores agent runs from simulator end state and traces only, tested on scripted runs through the real simulator and on hand-written traces.
- Built a benchmark grid runner with a content-addressed results cache, and a markdown results report.
- Found and worked around a PyLabRobot 0.2.2 bug where a failed aspirate or dispense still changed container volumes, with regression tests.
