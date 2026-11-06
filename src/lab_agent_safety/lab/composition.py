"""Track which analytes are in each container (D5).

PyLabRobot 0.2.2 tracks volumes only, so this sidecar tracks *amounts* of each analyte
(concentration x uL). Volume stays owned by PyLabRobot. A concentration is always
amount / current PLR volume, so the two records can't drift apart on volume.

Model: every container is well mixed. Moving V uL out of a container that held
V0 uL moves the fraction V / V0 of every analyte.
"""

TIP = "tip"  # the liquid inside the mounted tip is its own container


class Composition:
    def __init__(self) -> None:
        self._amounts: dict[str, dict[str, float]] = {}

    def fill(self, container: str, volume_ul: float, concentrations: dict[str, float]) -> None:
        """Set a container's starting contents (overwrites anything already recorded)."""
        self._amounts[container] = {a: c * volume_ul for a, c in concentrations.items()}

    def amounts(self, container: str) -> dict[str, float]:
        return dict(self._amounts.get(container, {}))

    def concentrations(self, container: str, volume_ul: float) -> dict[str, float]:
        if volume_ul <= 0:
            return {}
        return {a: amt / volume_ul for a, amt in self._amounts.get(container, {}).items()}

    def move(self, src: str, dst: str, volume_ul: float, src_volume_before_ul: float) -> None:
        """Move volume_ul of well-mixed liquid from src to dst."""
        if src_volume_before_ul <= 0 or volume_ul <= 0:
            return
        fraction = min(volume_ul / src_volume_before_ul, 1.0)
        src_amounts = self._amounts.setdefault(src, {})
        dst_amounts = self._amounts.setdefault(dst, {})
        for analyte, amount in list(src_amounts.items()):
            moved = amount * fraction
            src_amounts[analyte] = amount - moved
            dst_amounts[analyte] = dst_amounts.get(analyte, 0.0) + moved

    def clear(self, container: str) -> None:
        """Forget a container's contents (for example when a tip is dropped)."""
        self._amounts.pop(container, None)
