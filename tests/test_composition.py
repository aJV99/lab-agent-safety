import pytest

from lab_agent_safety.lab.composition import TIP, Composition


def test_move_is_proportional_and_conserves_amount():
    comp = Composition()
    comp.fill("stock", 1000, {"bsa": 2.0})  # 2000 amount units
    comp.move("stock", TIP, 100, src_volume_before_ul=1000)
    assert comp.amounts(TIP)["bsa"] == pytest.approx(200)
    assert comp.amounts("stock")["bsa"] == pytest.approx(1800)
    assert comp.concentrations("stock", 900)["bsa"] == pytest.approx(2.0)


def test_two_fold_dilution():
    comp = Composition()
    comp.fill("stock", 1000, {"bsa": 2.0})
    comp.move("stock", TIP, 100, 1000)
    comp.move(TIP, "well", 100, 100)        # 100 uL of 2 mg/mL into an empty well
    comp.fill("buffer", 1000, {})
    comp.move("buffer", TIP, 100, 1000)
    comp.move(TIP, "well", 100, 100)        # plus 100 uL buffer
    assert comp.concentrations("well", 200)["bsa"] == pytest.approx(1.0)


def test_empty_container_has_no_concentration():
    comp = Composition()
    assert comp.concentrations("nothing", 0) == {}
    comp.move("nothing", "well", 50, 0)  # moving from an empty container is a no-op
    assert comp.amounts("well") == {}
