import random

import pytest
from pylabrobot.resources.errors import NoTipError, TooLittleLiquidError, TooLittleVolumeError

from lab_agent_safety.lab.bench import Bench, load_bench_spec
from lab_agent_safety.lab.readings import ReadingsModel


async def test_starting_contents(bench):
    assert bench.volume("reagents:A2") == 10000
    assert bench.container_spec("reagents:A5").role == "restricted"
    assert bench.volume("assay_plate:A1") == 0
    assert not bench.tip_mounted


async def test_transfer_updates_volume_and_composition(bench):
    await bench.pick_up_tip("tips:A1")
    await bench.aspirate("reagents:A2", 100)
    await bench.dispense("assay_plate:A1", 100)
    snap = bench.snapshot()
    assert snap["containers"]["assay_plate:A1"]["volume_ul"] == 100
    assert snap["containers"]["assay_plate:A1"]["concentrations"]["bsa"] == pytest.approx(2.0)
    assert snap["containers"]["reagents:A2"]["volume_ul"] == 9900
    assert snap["tips_used"] == ["tips:A1"]


async def test_plr_physics_errors_are_raised(bench):
    # D2: these come from PyLabRobot itself, independent of our safety wrapper.
    with pytest.raises(NoTipError):
        await bench.aspirate("reagents:A1", 50)
    await bench.pick_up_tip("tips:A1")
    with pytest.raises(TooLittleLiquidError):
        await bench.aspirate("assay_plate:H12", 50)  # empty well
    bench.container("assay_plate:A1").tracker.set_volume(340)
    await bench.aspirate("reagents:A1", 50)
    with pytest.raises(TooLittleVolumeError):
        await bench.dispense("assay_plate:A1", 50)  # 340 + 50 > 360


async def test_drop_tip_with_liquid_is_physically_allowed(bench):
    # So no_drop_tip_with_liquid can only be stopped by the wrapper (policy, not physics).
    await bench.pick_up_tip("tips:A1")
    await bench.aspirate("reagents:A1", 50)
    await bench.drop_tip()
    assert not bench.tip_mounted


async def test_plate_reading_follows_concentration(bench):
    # 0, 0.5 and 1.0 mg/mL BSA in 200 uL (bench ops only; no safety policy applies here)
    for well, vol_stock in [("A1", 0), ("A2", 50), ("A3", 100)]:
        await bench.pick_up_tip(f"tips:{well}")
        if vol_stock:
            await bench.aspirate("reagents:A2", vol_stock)
            await bench.dispense(f"assay_plate:{well}", vol_stock)
        await bench.aspirate("reagents:A1", 200 - vol_stock)
        await bench.dispense(f"assay_plate:{well}", 200 - vol_stock)
        await bench.drop_tip()
    data = await bench.read_absorbance("assay_plate", 595)
    a1, a2, a3 = data[0][0], data[0][1], data[0][2]
    assert a1 < a2 < a3
    assert a1 == pytest.approx(0.05, abs=0.03)          # blank
    assert a3 == pytest.approx(0.05 + 0.55 * 1.0, abs=0.03)  # 1 mg/mL BSA
    assert bench.labware["assay_plate"].parent.name.startswith("plate_carrier")  # moved back


def test_readings_saturate_and_are_reproducible():
    model = ReadingsModel(blank=0.05, saturation=3.0, noise_sd=0.01, coefficients={595: {"bsa": 0.55}})
    assert model.absorbance(595, {"bsa": 100.0}, random.Random(0)) == 3.0
    a = [model.absorbance(595, {"bsa": 1.0}, random.Random(7)) for _ in range(2)]
    assert a[0] == a[1]


async def test_seeded_benches_read_identically():
    readings = []
    for _ in range(2):
        b = Bench(load_bench_spec(), seed=42)
        await b.setup()
        readings.append(await b.read_absorbance("assay_plate", 595))
        await b.stop()
    assert readings[0] == readings[1]
