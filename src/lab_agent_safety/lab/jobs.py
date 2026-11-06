"""Ordered job queue with device locks and no automatic retries (D6, D11).

Every physical command becomes a Job. Jobs run strictly one at a time, in the order
they were submitted (asyncio.Lock wakes waiters first-in, first-out). Each job also holds
the locks of the devices it touches, so nothing outside the queue can drive a device
mid-job.

There is deliberately no retry path. A job runs once and its outcome is final: a
failed physical action usually fails again, and a retry can be unsafe. Deciding what
to do after a failure is left to the operator (the agent), and the benchmark measures
whether it retries anyway.
"""

import asyncio
import time
from dataclasses import dataclass, field

from lab_agent_safety.lab.safety import Command, Outcome, SafetyWrapper

# Devices each operation physically touches. A plate read moves the plate off the
# liquid handler's deck, so it needs both.
DEVICES_TOUCHED = {
    "pick_up_tip": ("liquid_handler",),
    "drop_tip": ("liquid_handler",),
    "aspirate": ("liquid_handler",),
    "dispense": ("liquid_handler",),
    "read_plate": ("liquid_handler", "plate_reader"),
}


@dataclass
class Job:
    id: int
    command: Command
    submitted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    outcome: Outcome | None = None

    @property
    def state(self) -> str:
        if self.outcome is not None:
            return self.outcome.status  # executed / blocked / failed / invalid
        return "running" if self.started_at else "queued"

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "state": self.state,
            "command": self.command.__dict__,
            "outcome": self.outcome.to_dict() if self.outcome else None,
            "duration_s": round(self.finished_at - self.started_at, 4) if self.finished_at else None,
        }


@dataclass
class JobQueue:
    wrapper: SafetyWrapper
    jobs: list[Job] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._order = asyncio.Lock()
        self.device_locks = {"liquid_handler": asyncio.Lock(), "plate_reader": asyncio.Lock()}

    async def submit(self, cmd: Command) -> Job:
        """Queue a command, wait for it to run once, and return the finished job."""
        job = Job(id=len(self.jobs) + 1, command=cmd, submitted_at=time.time())
        self.jobs.append(job)
        async with self._order:
            locks = [self.device_locks[d] for d in sorted(DEVICES_TOUCHED[cmd.op])]
            for lock in locks:  # always acquired in the same (sorted) order: no deadlock
                await lock.acquire()
            try:
                job.started_at = time.time()
                job.outcome = await self.wrapper.execute(cmd)
                job.finished_at = time.time()
            finally:
                for lock in reversed(locks):
                    lock.release()
        return job

    def get(self, job_id: int) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)
