import math

import pytest

from shuttle_scout import units


def test_feet_meter_roundtrip():
    assert units.meters_to_feet(units.feet_to_meters(1000.0)) == pytest.approx(1000.0)
    assert units.feet_to_meters(1000.0) == pytest.approx(304.8, abs=0.01)


def test_miles_meters():
    assert units.miles_to_meters(0.25) == pytest.approx(402.336)
    assert units.meters_to_miles(1609.344) == pytest.approx(1.0)


def test_mph_to_mps():
    assert units.mph_to_mps(25.0) == pytest.approx(11.176, abs=0.001)


def test_grade_percent():
    assert units.grade_percent(100.0, 1000.0) == pytest.approx(10.0)
    assert units.grade_percent(500.0, 500.0) == pytest.approx(100.0)
    assert math.isinf(units.grade_percent(10.0, 0.0))


def test_slope_percent_to_degrees():
    assert units.slope_percent_to_degrees(100.0) == pytest.approx(45.0)
