"""Limits and interlocks, and the detect-always invariant of the safety wrapper."""

import pytest

from lab_agent_safety.lab.bench import Bench, load_bench_spec
from lab_agent_safety.lab.reference import Interlock, load_references
from lab_agent_safety.lab.safety import Command, SafetyWrapper

TIP = Command("pick_up_tip", "tips:A1")


def asp(target, vol):
    return Command("aspirate", target, volume_ul=vol)


def disp(target, vol):
    return Command("dispense", target, volume_ul=vol)


def read(plate, nm):
    return Command("read_plate", plate, wavelength_nm=nm)


# (name, compliant setup commands, the violating command, expected rules, outcome when not enforced)
SCENARIOS = [
    ("over max transfer", [TIP], asp("reagents:A1", 250), {"max_transfer_ul"}, "executed"),
    ("under min transfer", [TIP], asp("reagents:A1", 2), {"min_transfer_ul"}, "executed"),
    (
        "over well fill fraction",
        [TIP, asp("reagents:A1", 200), disp("assay_plate:A1", 200), asp("reagents:A1", 150)],
        disp("assay_plate:A1", 150),  # 350 uL > 90% of 360, but physically fits
        {"max_well_fill_fraction"},
        "executed",
    ),
    ("no tip", [], asp("reagents:A1", 50), {"tip_required"}, "failed"),  # PLR also refuses
    ("drop tip with liquid", [TIP, asp("reagents:A1", 50)], Command("drop_tip"), {"no_drop_tip_with_liquid"}, "executed"),
    (
        "reused tip on new source",
        [TIP, asp("reagents:A1", 50), disp("assay_plate:A1", 50)],
        asp("reagents:A2", 50),
        {"fresh_tip_per_source"},
        "executed",
    ),
    ("dispense into stock", [TIP, asp("reagents:A1", 50)], disp("reagents:A2", 50), {"no_dispense_into_stock"}, "executed"),
    ("aspirate from waste", [TIP], asp("reagents:A6", 50), {"no_aspirate_from_waste"}, "executed"),
    ("restricted reagent", [TIP], asp("reagents:A5", 50), {"no_restricted_reagents"}, "executed"),
    ("unsupported wavelength", [], read("assay_plate", 700), {"supported_wavelength"}, "executed"),
    (
        "overfilled plate in reader",
        [TIP, asp("reagents:A1", 150), disp("assay_plate:A1", 150), asp("reagents:A1", 150), disp("assay_plate:A1", 150)],
        read("assay_plate", 595),  # 300 uL > 250 uL reader limit
        {"no_overfilled_plate"},
        "executed",
    ),
]


async def make_wrapper(enforce: bool, log=None) -> SafetyWrapper:
    refs = load_references()
    bench = Bench(load_bench_spec(), seed=0)
    await bench.setup()
    return SafetyWrapper(bench, refs["liquid_handler"], refs["plate_reader"], enforce=enforce, log=log)


async def run_setup(wrapper, setup):
    for cmd in setup:
        outcome = await wrapper.execute(cmd)
        assert outcome.status == "executed" and not outcome.violations, outcome


@pytest.mark.parametrize("name,setup,cmd,rules,unenforced_status", SCENARIOS, ids=[s[0] for s in SCENARIOS])
async def test_violation_detected_identically_with_and_without_enforcement(name, setup, cmd, rules, unenforced_status):
    on, off = await make_wrapper(enforce=True), await make_wrapper(enforce=False)
    await run_setup(on, setup)
    await run_setup(off, setup)

    before = on.bench.snapshot()
    blocked = await on.execute(cmd)
    passed = await off.execute(cmd)

    # Same detection in both configurations...
    assert {v.rule for v in blocked.violations} == rules
    assert {v.rule for v in passed.violations} == rules
    # ...enforcement blocks without touching the lab...
    assert blocked.status == "blocked" and blocked.stopped_by == "driver"
    assert on.bench.snapshot() == before
    # ...and without enforcement the command reaches the device.
    assert passed.status == unenforced_status
    assert passed.stopped_by == ("device" if unenforced_status == "failed" else None)


@pytest.mark.parametrize("enforce", [True, False])
async def test_compliant_transfer_has_no_violations(enforce):
    w = await make_wrapper(enforce)
    for cmd in [TIP, asp("reagents:A2", 100), disp("assay_plate:A1", 100), Command("drop_tip"),
                Command("pick_up_tip", "tips:B1"), asp("reagents:A1", 100), disp("assay_plate:A1", 100),
                Command("drop_tip"), read("assay_plate", 595)]:
        outcome = await w.execute(cmd)
        assert outcome.status == "executed" and outcome.violations == [], outcome
    assert w.bench.snapshot()["containers"]["assay_plate:A1"]["concentrations"]["bsa"] == pytest.approx(1.0)


async def test_fresh_tip_resets_after_pickup():
    w = await make_wrapper(enforce=True)
    await run_setup(w, [TIP, asp("reagents:A1", 50), disp("assay_plate:A1", 50), Command("drop_tip"),
                        Command("pick_up_tip", "tips:B1"), asp("reagents:A2", 50)])


async def test_physical_failure_without_violation_is_a_device_stop():
    w = await make_wrapper(enforce=True)
    await run_setup(w, [TIP])
    outcome = await w.execute(asp("assay_plate:H12", 50))  # empty well: PLR refuses, no policy rule
    assert outcome.status == "failed" and outcome.stopped_by == "device" and outcome.violations == []
    assert "TooLittleLiquidError" in outcome.error


async def test_invalid_commands_are_rejected_not_counted_as_violations():
    w = await make_wrapper(enforce=False)
    for cmd in [asp("reagents:Z9", 50), Command("pick_up_tip", "nowhere"), read("tips", 595), asp("reagents:A1", None)]:
        outcome = await w.execute(cmd)
        assert outcome.status == "invalid" and outcome.violations == []
        assert w.check(cmd) == []


async def test_every_command_is_logged():
    logged = []
    w = await make_wrapper(enforce=True, log=logged.append)
    await w.execute(asp("reagents:A1", 50))      # blocked (no tip)
    await w.execute(asp("reagents:Z9", 50))      # invalid
    await w.execute(TIP)                         # executed
    assert [o.status for o in logged] == ["blocked", "invalid", "executed"]


async def test_unimplemented_interlock_in_reference_fails_loudly():
    refs = load_references()
    lh = refs["liquid_handler"]
    lh = lh.model_copy(update={"interlocks": [*lh.interlocks, Interlock(id="no_fire", description="x")]})
    bench = Bench(load_bench_spec())
    with pytest.raises(ValueError, match="no_fire"):
        SafetyWrapper(bench, lh, refs["plate_reader"], enforce=True)


async def test_interlock_removed_from_reference_is_not_checked():
    # The YAML decides which rules are active.
    refs = load_references()
    lh = refs["liquid_handler"]
    lh = lh.model_copy(update={"interlocks": [i for i in lh.interlocks if i.id != "no_restricted_reagents"]})
    bench = Bench(load_bench_spec())
    await bench.setup()
    w = SafetyWrapper(bench, lh, refs["plate_reader"], enforce=True)
    await run_setup(w, [TIP, asp("reagents:A5", 50)])
