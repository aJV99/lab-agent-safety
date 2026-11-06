"""Benchmark tasks: one YAML file per task in benchmark/tasks/.

A task is a prompt, the bench's starting contents, and what a correct end state looks
like. The grader (grader.py) checks a run against it using only the simulator's end
state and the traces.

    category   normal     the task is safe and should simply be done
               dangerous  doing the task as asked would break a rule; the safe result is
                          a refusal, a block, or a safe workaround
    kind       (dangerous only) explicit: the prompt asks for the unsafe action outright;
               implicit: the obvious way to do a reasonable request breaks a rule
    target_rules  (dangerous only) the rule(s) the task is designed to test. Reported for
               analysis; pass/fail counts violations of *any* rule.
    expect     end-state checks. For a normal task they define success. For a dangerous
               task they are optional and define a "safe completion" (the task done
               within the rules); without them the only safe outcome is not doing it.
"""

from importlib import resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from lab_agent_safety.lab.bench import BenchSpec, ContainerSpec


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Range = tuple[float, float]  # inclusive [low, high]


class ContainerExpectation(_Strict):
    volume_ul: Range
    # Analytes listed must fall in their range; any analyte *not* listed must be absent.
    # So a well that should hold plain PBS fails if it picked up any BSA or azide.
    concentrations: dict[str, Range] = {}


class ReadExpectation(_Strict):
    """An executed plate read, after the last executed dispense into that plate."""

    plate: str
    wavelength_nm: int | None = None  # None: any wavelength


class Expectation(_Strict):
    containers: dict[str, ContainerExpectation] = {}
    reads: list[ReadExpectation] = []


class Task(_Strict):
    id: str
    category: Literal["normal", "dangerous"]
    kind: Literal["explicit", "implicit"] | None = None
    target_rules: list[str] = []
    source: str  # where the procedure comes from, or why the task exists
    prompt: str
    bench: dict[str, ContainerSpec] = {}  # starting contents that differ from bench.yaml
    expect: Expectation | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "Task":
        if self.category == "normal":
            if self.expect is None:
                raise ValueError(f"{self.id}: a normal task needs `expect`")
            if self.kind or self.target_rules:
                raise ValueError(f"{self.id}: `kind` and `target_rules` are for dangerous tasks")
        else:
            if self.kind is None or not self.target_rules:
                raise ValueError(f"{self.id}: a dangerous task needs `kind` and `target_rules`")
        return self

    def bench_spec(self, base: BenchSpec) -> BenchSpec:
        return base.with_containers(self.bench)


DEFAULT_TASKS_DIR = Path(str(resources.files("lab_agent_safety.benchmark") / "tasks"))


def load_task(path: Path) -> Task:
    task = Task.model_validate(yaml.safe_load(path.read_text()))
    if task.id != path.stem:
        raise ValueError(f"{path}: task id {task.id!r} does not match the file name")
    return task


def load_tasks(directory: Path = DEFAULT_TASKS_DIR) -> dict[str, Task]:
    """All tasks, normal first, then by id (a stable order for the run grid and report)."""
    tasks = [load_task(p) for p in directory.glob("*.yaml")]
    tasks.sort(key=lambda t: (t.category != "normal", t.id))
    return {t.id: t for t in tasks}
