"""Validated scientific settings bound to one MS Event Studio project."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


DEFAULT_PRIMARY_MARKER_MZ = 760.5851
QC_MARKER_MZ = 782.5616
MARKER_TOLERANCE_PPM = 12.0
DEFAULT_COLLISION_GAP_SEC = 0.60

MIN_PRIMARY_MARKER_MZ = 1.0
MAX_PRIMARY_MARKER_MZ = 5000.0
MIN_COLLISION_GAP_SEC = 0.01
MAX_COLLISION_GAP_SEC = 60.0


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


@dataclass(frozen=True, slots=True)
class ProjectScientificSettings:
    primary_marker_mz: float = DEFAULT_PRIMARY_MARKER_MZ
    marker_tolerance_ppm: float = MARKER_TOLERANCE_PPM
    collision_gap_sec: float = DEFAULT_COLLISION_GAP_SEC

    def __post_init__(self) -> None:
        primary = _finite_number(self.primary_marker_mz, "primary_marker_mz")
        tolerance = _finite_number(self.marker_tolerance_ppm, "marker_tolerance_ppm")
        collision = _finite_number(self.collision_gap_sec, "collision_gap_sec")
        if not MIN_PRIMARY_MARKER_MZ <= primary <= MAX_PRIMARY_MARKER_MZ:
            raise ValueError(
                f"primary_marker_mz must be between {MIN_PRIMARY_MARKER_MZ:g} and "
                f"{MAX_PRIMARY_MARKER_MZ:g}"
            )
        if tolerance != MARKER_TOLERANCE_PPM:
            raise ValueError(f"marker_tolerance_ppm is fixed at {MARKER_TOLERANCE_PPM:g}")
        if not MIN_COLLISION_GAP_SEC <= collision <= MAX_COLLISION_GAP_SEC:
            raise ValueError(
                f"collision_gap_sec must be between {MIN_COLLISION_GAP_SEC:g} and "
                f"{MAX_COLLISION_GAP_SEC:g}"
            )
        object.__setattr__(self, "primary_marker_mz", primary)
        object.__setattr__(self, "marker_tolerance_ppm", tolerance)
        object.__setattr__(self, "collision_gap_sec", collision)

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_manifest(cls, value: object) -> ProjectScientificSettings:
        if not isinstance(value, dict) or set(value) != {
            "primary_marker_mz",
            "marker_tolerance_ppm",
            "collision_gap_sec",
        }:
            raise ValueError("project scientific settings are missing or invalid")
        return cls(**value)
