"""Load and validate the device reference files (devices/*.yaml).

A reference file is the single source of truth for what a device can do and what it
must never do. Three consumers read it:
  - the describe_device MCP tool (the agent reads it on demand),
  - the prompt-rules safety layer (renders it into the system prompt),
  - the safety wrapper (enforces the limits and interlocks).

The models are strict (extra="forbid"): a misspelt key is a load error, not a
silently missing limit.
"""

from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Interlock(_Strict):
    id: str
    description: str


class LiquidHandlerCapabilities(_Strict):
    operations: list[str]
    channels: int = Field(gt=0)
    tip_capacity_ul: float = Field(gt=0)


class LiquidHandlerLimits(_Strict):
    min_transfer_ul: float = Field(gt=0)
    max_transfer_ul: float = Field(gt=0)
    max_well_fill_fraction: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _min_below_max(self):
        if self.min_transfer_ul >= self.max_transfer_ul:
            raise ValueError("min_transfer_ul must be below max_transfer_ul")
        return self


class PlateReaderCapabilities(_Strict):
    operations: list[str]
    plate_formats: list[str]


class PlateReaderLimits(_Strict):
    allowed_wavelengths_nm: list[int] = Field(min_length=1)
    max_well_volume_ul: float = Field(gt=0)


class _DeviceBase(_Strict):
    id: str
    name: str
    description: str
    interlocks: list[Interlock]

    @model_validator(mode="after")
    def _unique_interlocks(self):
        ids = [i.id for i in self.interlocks]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate interlock ids in {self.id}: {ids}")
        return self

    def interlock_ids(self) -> set[str]:
        return {i.id for i in self.interlocks}


class LiquidHandlerReference(_DeviceBase):
    kind: Literal["liquid_handler"]
    capabilities: LiquidHandlerCapabilities
    limits: LiquidHandlerLimits


class PlateReaderReference(_DeviceBase):
    kind: Literal["plate_reader"]
    capabilities: PlateReaderCapabilities
    limits: PlateReaderLimits


DeviceReference = Annotated[
    LiquidHandlerReference | PlateReaderReference, Field(discriminator="kind")
]
_adapter = TypeAdapter(DeviceReference)

DEFAULT_DEVICES_DIR = Path(str(resources.files("lab_agent_safety.lab") / "devices"))


def load_reference(path: Path) -> LiquidHandlerReference | PlateReaderReference:
    """Parse and validate one reference file."""
    return _adapter.validate_python(yaml.safe_load(path.read_text()))


def load_references(
    devices_dir: Path = DEFAULT_DEVICES_DIR,
) -> dict[str, LiquidHandlerReference | PlateReaderReference]:
    """Load every *.yaml in a directory, keyed by device id."""
    refs = {}
    for path in sorted(devices_dir.glob("*.yaml")):
        ref = load_reference(path)
        if ref.id in refs:
            raise ValueError(f"duplicate device id {ref.id!r} in {path}")
        refs[ref.id] = ref
    return refs


def reference_text(device_id: str, devices_dir: Path = DEFAULT_DEVICES_DIR) -> str:
    """The raw YAML of one reference file, exactly as the agent should see it."""
    for path in sorted(devices_dir.glob("*.yaml")):
        if load_reference(path).id == device_id:
            return path.read_text()
    raise KeyError(device_id)
