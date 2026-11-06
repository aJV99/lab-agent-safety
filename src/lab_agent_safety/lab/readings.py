"""A thin absorbance model for PyLabRobot's simulated plate reader.

PyLabRobot's PlateReaderChatterboxBackend returns fixed placeholder arrays (all zeros).
This subclass computes a reading for each well from what is actually in it:

    A = blank + sum(coefficient[wavelength][analyte] * concentration[analyte]) + noise

clipped to [0, saturation]. It's a linear Beer-Lambert-style model. It is deliberately
not chemically faithful (for example, protein absorbs at 595 nm here even without dye).
Readings exist so that read steps produce meaningful, reproducible numbers. Grading
uses the simulator's end state, not these numbers.
"""

import random
import time
from collections.abc import Callable
from typing import Optional

from pydantic import BaseModel, ConfigDict
from pylabrobot.plate_reading.chatterbox import PlateReaderChatterboxBackend
from pylabrobot.resources import Plate, Well


class ReadingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    blank: float
    saturation: float
    noise_sd: float
    coefficients: dict[int, dict[str, float]]

    def absorbance(self, wavelength: int, concentrations: dict[str, float], rng: random.Random) -> float:
        coeffs = self.coefficients.get(wavelength, {})
        signal = sum(coeffs.get(analyte, 0.0) * c for analyte, c in concentrations.items())
        value = self.blank + signal + rng.gauss(0.0, self.noise_sd)
        return round(min(max(value, 0.0), self.saturation), 4)


class ReadingsPlateReaderBackend(PlateReaderChatterboxBackend):
    """Chatterbox plate reader whose absorbance depends on well contents."""

    def __init__(
        self,
        model: ReadingsModel,
        concentrations_of: Callable[[Well], dict[str, float]],
        seed: int,
    ) -> None:
        super().__init__()
        self.model = model
        self.concentrations_of = concentrations_of
        self.rng = random.Random(seed)

    async def read_absorbance(self, plate: Plate, wells: list[Well], wavelength: int) -> list[dict]:
        data: list[list[Optional[float]]] = [
            [None] * plate.num_items_x for _ in range(plate.num_items_y)
        ]
        for well in wells:
            value = self.model.absorbance(wavelength, self.concentrations_of(well), self.rng)
            data[well.get_row()][well.get_column()] = value
        return [{"time": time.time(), "temperature": float("nan"), "data": data, "wavelength": wavelength}]
