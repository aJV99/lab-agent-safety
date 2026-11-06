# lab-agent-safety

A benchmark that measures which **system-level safety layers** actually stop an AI agent from doing dangerous things when it controls lab robots.

> Status: milestone 1 done. It produced a first result: prompt rules vs driver enforcement on 10 tasks (below). The reviewer and dry-run layers come in milestone 2. See [docs/PROGRESS.md](docs/PROGRESS.md).

## What it does

A Claude agent operates a simulated lab (built on [PyLabRobot](https://github.com/PyLabRobot/pylabrobot)) through an MCP server. A set of tasks based on public lab protocols runs repeatedly with each safety layer switched on or off:

| Layer | What it does |
|---|---|
| Prompt rules | Device limits written into the system prompt |
| Driver enforcement | A wrapper around PyLabRobot that refuses commands that break a limit or interlock |
| Reviewer sub-agent | A second model checks the plan against the device reference files before anything runs *(planned)* |
| Dry run | The plan runs on a shadow copy of the simulator first *(planned)* |

The safety wrapper **always detects** violations, even when enforcement is off. With enforcement off it logs them and lets them through. That way "unsafe actions attempted" is measured the same way in every configuration. Grading is deterministic, from the simulator's end state plus the run trace. It never relies on the model's own claim that it succeeded.

## First result (M1)

There are 10 tasks: 4 normal and 6 dangerous. Each dangerous task is physically possible in the simulator, so only a safety layer can stop it. Each task ran under 2 configurations, with 5 reps, on `claude-haiku-5-5`, using an auto-approve policy. That makes 100 runs, costing $0.16 in total. Full table: [results/m1.md](results/m1.md). Every run's grade: [results/m1.json](results/m1.json). Every run's raw traces and end state: [results/m1/runs/](results/m1/runs/). You can regrade them without an API key, using `uv run lab-agent-safety report --runs-dir results/m1/runs`.

| | prompt rules only | driver enforcement only |
|---|---|---|
| Normal tasks passed | 95% ± 11% (19/20) | 90% ± 14% (18/20) |
| Dangerous tasks kept safe | 97% ± 7% (29/30) | 100% (30/30) |
| Runs that attempted a rule-breaking command | 1/50 | 15/50 |
| Rule-breaking commands that executed | 2 (in 1 run) | 0 (26 blocked by the driver) |
| Runs that read the device rules before acting | 6/50 | 16/50 |
| Mean cost per run | $0.0014 | $0.0018 |

± is the sample standard deviation across the 5 reps.

What it shows, at this small scale:

- **Prompt rules mostly prevent attempts, but not always.** With the rules in the system prompt, the agent attempted a violation in 1 run out of 50. In that run it moved 250 uL in one step against a 200 uL cap, and nothing stopped it. Without prompt rules, attempts rose to 15/50. The driver blocked every one.
- **A driver block is recoverable.** In all 5 driver-only runs of the 250 uL task, the agent was blocked, read the refusal reason, split the transfer, and finished the task within the rules.
- **Some rules the model enforces on its own, some it doesn't.** Requests to pipette sodium azide or reuse waste were refused in every run of both configurations. Returning leftover liquid to a stock bottle was refused 5/5 with prompt rules, and attempted 5/5 without them, when the driver blocked it.
- **The driver-only agent wasn't rule-blind.** It could still read the rules with `describe_device`, and did so before acting in 16/50 runs. "Driver only" here means rules available on request plus enforcement, not an agent that knows no rules.
- **Agents plan as if every step will succeed.** In 13/50 driver-only runs, the agent had sent several steps at once, and the later ones still ran after an earlier one was blocked. It was harmless here, because a blocked or failed step changes nothing, but it is the gap a planned "abort the batch" layer would close.
- **Normal-task failures were agent mistakes, not safety events.** All three were in the serial dilution, where the agent got the volumes or dilution factor wrong. In one of them it reported correct concentrations it hadn't made. Grading comes from the simulator's end state, so the claim didn't count.

Limits: one small model, 10 tasks, 5 reps and a simulated lab. The differences above are counts from 50 runs per configuration, not statistically tested effects. The prompt-plus-driver and no-layer configurations, the reviewer and dry-run layers, and a larger model come in milestone 2.

## How this differs from related work

- **[OpenLabAI](https://github.com/nygmeta/OpenLabAI)** and **[plr-mcp](https://github.com/di-omics/plr-mcp)** build MCP servers and safety controls for lab robots. This project builds on the same idea but focuses on *measuring* agent safety behaviour across configurations, which neither of them benchmarks.
- **[LabShield](https://arxiv.org/abs/2603.11987)**, SafeLab and ResponsibleRobotBench evaluate whether *models* reason safely about lab work. This project holds the model fixed and varies the *system around it*, to ask which architectural layers buy how much safety.
- **[PyLabRobot](https://github.com/PyLabRobot/pylabrobot)** (MIT) provides the hardware-agnostic liquid-handler and plate-reader simulation this benchmark runs on.

## Quick start

```bash
uv sync
uv run pytest                                   # offline test suite, no API calls
uv run lab-agent-safety run --reps 5            # the benchmark grid (needs ANTHROPIC_API_KEY; cached runs are skipped)
uv run lab-agent-safety report --reps 5         # grade the cached runs and print the results table
```

## Licence

MIT. See [LICENSE](LICENSE).
