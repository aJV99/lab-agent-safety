"""The simulated bench: a PyLabRobot liquid handler and plate reader built from bench.yaml.

This module only *builds and observes* the lab. It applies no safety rules (that is
safety.py) and has no queueing (that is jobs.py). PyLabRobot's volume and tip tracking
are always switched on (D2), so physically impossible commands raise PLR errors.

Containers are addressed as "<labware>:<well>", for example "assay_plate:B3".
"""

from importlib import resources
from pathlib import Path
from typing import Literal

import pylabrobot.resources as plr_resources
import yaml
from pydantic import BaseModel, ConfigDict, Field
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
from pylabrobot.plate_reading import PlateReader
from pylabrobot.resources import (
    PLT_CAR_L5AC_A00,
    TIP_CAR_480_A00,
    Container,
    Plate,
    STARLetDeck,
    TipSpot,
    set_tip_tracking,
    set_volume_tracking,
)

from lab_agent_safety.lab.composition import TIP, Composition
from lab_agent_safety.lab.readings import ReadingsModel, ReadingsPlateReaderBackend

Role = Literal["stock", "restricted", "waste", "work"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabwareSpec(_Strict):
    factory: str  # name of a PyLabRobot resource factory, e.g. "cor_96_wellplate_360uL_Fb"
    carrier: Literal["tip_carrier", "plate_carrier"]
    slot: int = Field(ge=0)


class ContainerSpec(_Strict):
    role: Role = "work"
    liquid: str = ""
    volume_ul: float = Field(default=0.0, ge=0)
    concentrations: dict[str, float] = {}


class BenchSpec(_Strict):
    labware: dict[str, LabwareSpec]
    containers: dict[str, ContainerSpec]
    readings: ReadingsModel

    def with_containers(self, overrides: dict[str, ContainerSpec]) -> "BenchSpec":
        """A copy with some containers replaced (tasks use this to set starting contents)."""
        return self.model_copy(update={"containers": {**self.containers, **overrides}})


DEFAULT_BENCH = Path(str(resources.files("lab_agent_safety.lab") / "bench.yaml"))


def load_bench_spec(path: Path = DEFAULT_BENCH) -> BenchSpec:
    return BenchSpec.model_validate(yaml.safe_load(path.read_text()))


def _well_ids(labware) -> list[str]:
    return [
        f"{chr(ord('A') + row)}{col + 1}"
        for row in range(labware.num_items_y)
        for col in range(labware.num_items_x)
    ]


class Bench:
    # Off-deck size of the reader resource; the value only matters for PLR's geometry.
    _READER_SIZE = (130.0, 90.0, 60.0)

    def __init__(self, spec: BenchSpec, seed: int = 0) -> None:
        set_volume_tracking(True)
        set_tip_tracking(True)

        self.spec = spec
        self.composition = Composition()
        self.lh = LiquidHandler(backend=LiquidHandlerChatterboxBackend(num_channels=1), deck=STARLetDeck())

        carriers = {
            "tip_carrier": TIP_CAR_480_A00(name="tip_carrier"),
            "plate_carrier": PLT_CAR_L5AC_A00(name="plate_carrier"),
        }
        self.labware = {}
        for name, lw in spec.labware.items():
            resource = getattr(plr_resources, lw.factory)(name=name)
            carriers[lw.carrier][lw.slot] = resource
            self.labware[name] = resource
        self.lh.deck.assign_child_resource(carriers["tip_carrier"], rails=3)
        self.lh.deck.assign_child_resource(carriers["plate_carrier"], rails=9)

        # name <-> PLR object, for every well and tip spot
        self._by_name: dict[str, Container | TipSpot] = {}
        self._name_of: dict[int, str] = {}
        for lw_name, lw in self.labware.items():
            for well_id in _well_ids(lw):
                item = lw.get_item(well_id)
                self._by_name[f"{lw_name}:{well_id}"] = item
                self._name_of[id(item)] = f"{lw_name}:{well_id}"

        for name, c in spec.containers.items():
            container = self.container(name)
            container.tracker.set_volume(c.volume_ul)
            self.composition.fill(name, c.volume_ul, c.concentrations)

        self.reader = PlateReader(
            "plate_reader",
            *self._READER_SIZE,
            backend=ReadingsPlateReaderBackend(spec.readings, self._well_concentrations, seed),
        )

    async def setup(self) -> None:
        await self.lh.setup()
        await self.reader.setup()

    async def stop(self) -> None:
        await self.lh.stop()
        await self.reader.stop()

    # ---- lookups -------------------------------------------------------------------

    def container(self, name: str) -> Container:
        item = self._by_name.get(name)
        if not isinstance(item, Container):
            raise KeyError(f"no container named {name!r}")
        return item

    def tip_spot(self, name: str) -> TipSpot:
        item = self._by_name.get(name)
        if not isinstance(item, TipSpot):
            raise KeyError(f"no tip spot named {name!r}")
        return item

    def name_of(self, resource) -> str:
        return self._name_of[id(resource)]

    def container_spec(self, name: str) -> ContainerSpec:
        return self.spec.containers.get(name, ContainerSpec())

    def volume(self, name: str) -> float:
        return self.container(name).tracker.get_used_volume()

    def capacity(self, name: str) -> float:
        return self.container(name).max_volume

    def plate_names(self) -> list[str]:
        return [n for n, lw in self.labware.items() if isinstance(lw, Plate)]

    def plate_containers(self, plate: str) -> list[str]:
        return [f"{plate}:{w}" for w in _well_ids(self.labware[plate])]

    @property
    def tip_mounted(self) -> bool:
        return self.lh.head[0].has_tip

    @property
    def tip_volume(self) -> float:
        return self.lh.head[0].get_tip().tracker.get_used_volume() if self.tip_mounted else 0.0

    def _well_concentrations(self, well) -> dict[str, float]:
        return self.composition.concentrations(self.name_of(well), well.tracker.get_used_volume())

    # ---- device operations (no safety checks here) ---------------------------------

    async def pick_up_tip(self, spot: str) -> None:
        await self.lh.pick_up_tips([self.tip_spot(spot)], use_channels=[0])

    async def drop_tip(self) -> None:
        # PLR's discard_tips physically allows dropping a tip that still holds liquid.
        await self.lh.discard_tips(use_channels=[0], allow_nonzero_volume=True)
        self.composition.clear(TIP)

    async def aspirate(self, source: str, volume_ul: float) -> None:
        before = self.volume(source)
        await self._transfer(self.lh.aspirate, source, volume_ul)
        self.composition.move(source, TIP, volume_ul, before)

    async def dispense(self, dest: str, volume_ul: float) -> None:
        before = self.tip_volume
        await self._transfer(self.lh.dispense, dest, volume_ul)
        self.composition.move(TIP, dest, volume_ul, before)

    async def _transfer(self, operation, name: str, volume_ul: float) -> None:
        """Run a PLR aspirate or dispense; if it fails, undo its half-applied volume change.

        PLR 0.2.2 queues the container's volume change before the tip's. When the tip
        side then raises (an empty tip on dispense, an overfull tip on aspirate), PLR
        never rolls back the container's pending change, and the next successful
        operation commits it: liquid appears from nothing or vanishes (D19). PLR commits
        after every success, so rolling both trackers back restores the exact state
        from before this command.
        """
        container = self.container(name)
        try:
            await operation([container], vols=[volume_ul], use_channels=[0])
        except Exception:
            container.tracker.rollback()
            if self.tip_mounted:
                self.lh.head[0].get_tip().tracker.rollback()
            raise

    async def read_absorbance(self, plate: str, wavelength: int) -> list[list[float | None]]:
        """Move the plate into the reader, read every well, move it back."""
        resource = self.labware[plate]
        site = resource.parent
        resource.unassign()
        self.reader.assign_child_resource(resource)
        try:
            result = await self.reader.read_absorbance(wavelength=wavelength, use_new_return_type=True)
        finally:
            resource.unassign()
            site.assign_child_resource(resource)
        return result[0]["data"]

    # ---- observation ---------------------------------------------------------------

    def snapshot(self) -> dict:
        """End-state record for the grader: every non-empty or named container, and the tip."""
        containers = {}
        for name, item in self._by_name.items():
            if not isinstance(item, Container):
                continue
            volume = item.tracker.get_used_volume()
            if volume <= 0 and name not in self.spec.containers:
                continue
            spec = self.container_spec(name)
            containers[name] = {
                "role": spec.role,
                "liquid": spec.liquid,
                "volume_ul": round(volume, 4),
                "concentrations": {
                    a: round(c, 6) for a, c in self.composition.concentrations(name, volume).items()
                },
            }
        return {
            "containers": containers,
            "tip": {
                "mounted": self.tip_mounted,
                "volume_ul": round(self.tip_volume, 4),
                "concentrations": self.composition.concentrations(TIP, self.tip_volume),
            },
            "tips_used": sorted(
                n for n, item in self._by_name.items()
                if isinstance(item, TipSpot) and not item.tracker.has_tip
            ),
        }
