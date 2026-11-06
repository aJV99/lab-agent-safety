from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from lab_agent_safety.lab.reference import (
    LiquidHandlerReference,
    PlateReaderReference,
    load_reference,
    load_references,
    reference_text,
)


def test_shipped_reference_files_load():
    refs = load_references()
    assert set(refs) == {"liquid_handler", "plate_reader"}
    assert isinstance(refs["liquid_handler"], LiquidHandlerReference)
    assert isinstance(refs["plate_reader"], PlateReaderReference)


def test_policy_cap_is_below_physical_tip_capacity():
    # Dangerous tasks rely on over-policy transfers being physically possible (D2).
    lh = load_references()["liquid_handler"]
    assert lh.limits.max_transfer_ul < lh.capabilities.tip_capacity_ul


def test_reference_text_is_the_raw_file():
    text = reference_text("plate_reader")
    assert "allowed_wavelengths_nm" in text
    with pytest.raises(KeyError):
        reference_text("centrifuge")


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "device.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _lh_data() -> dict:
    return yaml.safe_load(
        (Path(__file__).parents[1] / "src/lab_agent_safety/lab/devices/liquid_handler.yaml").read_text()
    )


def test_misspelt_limit_is_rejected(tmp_path):
    data = _lh_data()
    data["limits"]["max_tranfer_ul"] = data["limits"].pop("max_transfer_ul")
    with pytest.raises(ValidationError):
        load_reference(_write(tmp_path, data))


def test_min_above_max_is_rejected(tmp_path):
    data = _lh_data()
    data["limits"]["min_transfer_ul"] = 500
    with pytest.raises(ValidationError):
        load_reference(_write(tmp_path, data))


def test_duplicate_interlock_is_rejected(tmp_path):
    data = _lh_data()
    data["interlocks"].append(dict(data["interlocks"][0]))
    with pytest.raises(ValidationError):
        load_reference(_write(tmp_path, data))


def test_unknown_kind_is_rejected(tmp_path):
    data = _lh_data()
    data["kind"] = "centrifuge"
    with pytest.raises(ValidationError):
        load_reference(_write(tmp_path, data))
