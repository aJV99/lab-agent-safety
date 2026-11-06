import asyncio

from lab_agent_safety.lab.bench import Bench, load_bench_spec
from lab_agent_safety.lab.jobs import JobQueue
from lab_agent_safety.lab.reference import load_references
from lab_agent_safety.lab.safety import Command, SafetyWrapper


async def make_queue(enforce=True) -> JobQueue:
    refs = load_references()
    bench = Bench(load_bench_spec())
    await bench.setup()
    return JobQueue(SafetyWrapper(bench, refs["liquid_handler"], refs["plate_reader"], enforce=enforce))


async def test_jobs_run_in_submission_order_one_at_a_time():
    q = await make_queue()
    running = []
    real_execute = q.wrapper.execute

    async def slow_execute(cmd):
        running.append(cmd.op)
        assert len(running) == 1, "two jobs overlapped"
        await asyncio.sleep(0.01)
        running.pop()
        return await real_execute(cmd)

    q.wrapper.execute = slow_execute
    cmds = [
        Command("pick_up_tip", "tips:A1"),
        Command("aspirate", "reagents:A1", volume_ul=100),
        Command("dispense", "assay_plate:A1", volume_ul=100),
        Command("read_plate", "assay_plate", wavelength_nm=595),
    ]
    jobs = await asyncio.gather(*(q.submit(c) for c in cmds))
    assert [j.id for j in jobs] == [1, 2, 3, 4]
    assert [j.state for j in jobs] == ["executed"] * 4
    assert all(a.finished_at <= b.started_at for a, b in zip(jobs, jobs[1:]))


async def test_failed_job_is_final_and_never_retried():
    q = await make_queue()
    calls = []
    real_execute = q.wrapper.execute

    async def counting_execute(cmd):
        calls.append(cmd)
        return await real_execute(cmd)

    q.wrapper.execute = counting_execute
    await q.submit(Command("pick_up_tip", "tips:A1"))
    job = await q.submit(Command("aspirate", "assay_plate:H12", volume_ul=50))  # empty well
    assert job.state == "failed"
    assert len(calls) == 2  # executed exactly once
    assert q.get(job.id).outcome.stopped_by == "device"


async def test_device_locks_released_after_failure():
    q = await make_queue()
    await q.submit(Command("aspirate", "reagents:A1", volume_ul=50))  # blocked: no tip
    assert not any(lock.locked() for lock in q.device_locks.values())


async def test_job_history_is_serialisable():
    q = await make_queue()
    job = await q.submit(Command("pick_up_tip", "tips:A1"))
    d = job.to_dict()
    assert d["job_id"] == 1 and d["state"] == "executed" and d["command"]["target"] == "tips:A1"
