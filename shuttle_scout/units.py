"""Unit conversions and grade math.

Convention used throughout the package:

* All raster / geometry math happens in **meters** in a projected CRS.
* All user-facing configuration is in **feet, miles, minutes** (US trail
  vernacular), converted to meters/seconds exactly once in ``Config``.
* Grades are **percent** (100 * rise / run), always computed from a
  positive "drop" and a horizontal "run"; direction of travel is handled
  by the caller.

Keeping every conversion in this module (and nowhere else) is what makes
the unit-consistency guarantee auditable.
"""

from __future__ import annotations

FEET_PER_METER = 3.280839895  # exact-enough international foot
METERS_PER_MILE = 1609.344


def feet_to_meters(feet: float) -> float:
    return feet / FEET_PER_METER


def meters_to_feet(meters: float) -> float:
    return meters * FEET_PER_METER


def miles_to_meters(miles: float) -> float:
    return miles * METERS_PER_MILE


def meters_to_miles(meters: float) -> float:
    return meters / METERS_PER_MILE


def mph_to_mps(mph: float) -> float:
    return mph * METERS_PER_MILE / 3600.0


def grade_percent(drop_m: float, run_m: float) -> float:
    """Grade as a percentage from a vertical drop and horizontal run.

    Both arguments are meters (or any single consistent unit). A zero or
    negative run returns ``float('inf')`` — callers treat that as "cliff",
    which correctly fails the <=100% hard constraint.
    """
    if run_m <= 0:
        return float("inf")
    return 100.0 * drop_m / run_m


def slope_percent_to_degrees(slope_pct: float) -> float:
    import math

    return math.degrees(math.atan(slope_pct / 100.0))
