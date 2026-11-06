# lab-agent-safety

A benchmark that measures which **system-level safety layers** actually stop an AI agent from doing dangerous things when it controls lab robots.

> Status: early development (milestone 1). No benchmark results yet. See [docs/PROGRESS.md](docs/PROGRESS.md).

## What it does

A Claude agent operates a simulated lab (built on [PyLabRobot](https://github.com/PyLabRobot/pylabrobot)) through an MCP server. A set of tasks based on public lab protocols runs repeatedly with each safety layer switched on or off:

| Layer | What it does |
|---|---|
| Prompt rules | Device limits written into the system prompt |
| Driver enforcement | A wrapper around PyLabRobot that refuses commands that break a limit or interlock |
| Reviewer sub-agent | A second model checks the plan against the device reference files before anything runs *(planned)* |
| Dry run | The plan runs on a shadow copy of the simulator first *(planned)* |

The safety wrapper **always detects** violations, even when enforcement is off. With enforcement off it logs them and lets them through. That way "unsafe actions attempted" is measured the same way in every configuration. Grading is deterministic, from the simulator's end state plus the run trace. It never relies on the model's own claim that it succeeded.

## How this differs from related work

- **[OpenLabAI](https://github.com/nygmeta/OpenLabAI)** and **[plr-mcp](https://github.com/di-omics/plr-mcp)** build MCP servers and safety controls for lab robots. This project builds on the same idea but focuses on *measuring* agent safety behaviour across configurations, which neither of them benchmarks.
- **[LabShield](https://arxiv.org/abs/2603.11987)**, SafeLab and ResponsibleRobotBench evaluate whether *models* reason safely about lab work. This project holds the model fixed and varies the *system around it*, to ask which architectural layers buy how much safety.
- **[PyLabRobot](https://github.com/PyLabRobot/pylabrobot)** (MIT) provides the hardware-agnostic liquid-handler and plate-reader simulation this benchmark runs on.

## Quick start

```bash
uv sync
uv run pytest
```

## Licence

MIT. See [LICENSE](LICENSE).
