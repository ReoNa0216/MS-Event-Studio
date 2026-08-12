"""Integer-nanosecond time primitives.

Decimal input is converted once at the boundary.  Internal range ownership never
depends on binary floating-point comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN


NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_MINUTE = 60 * NANOSECONDS_PER_SECOND


def _decimal(value: str | int | float | Decimal) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid finite time value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"invalid finite time value: {value!r}")
    return result


def minutes_to_ns(value: str | int | float | Decimal) -> int:
    """Convert minutes to the nearest integer nanosecond deterministically."""

    scaled = _decimal(value) * Decimal(NANOSECONDS_PER_MINUTE)
    return int(scaled.to_integral_value(rounding=ROUND_HALF_EVEN))


def seconds_to_ns(value: str | int | float | Decimal) -> int:
    scaled = _decimal(value) * Decimal(NANOSECONDS_PER_SECOND)
    return int(scaled.to_integral_value(rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True, slots=True)
class AnalysisRange:
    """A closed analysis interval in integer nanoseconds."""

    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if isinstance(self.start_ns, bool) or isinstance(self.end_ns, bool):
            raise TypeError("analysis range boundaries must be integer nanoseconds")
        if int(self.end_ns) < int(self.start_ns):
            raise ValueError("analysis range end precedes start")

    @classmethod
    def from_minutes(
        cls,
        start: str | int | float | Decimal,
        end: str | int | float | Decimal,
    ) -> "AnalysisRange":
        return cls(minutes_to_ns(start), minutes_to_ns(end))

    def contains_ns(self, value: int) -> bool:
        return self.start_ns <= int(value) <= self.end_ns

    def as_dict(self) -> dict[str, int | str]:
        return {
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "boundary_rule": "closed_current_apex_v1",
        }
