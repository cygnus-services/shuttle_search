import numpy as np
import pytest

from shuttle_scout import units
from shuttle_scout.candidates import CandidatePoint, screen_pairs
from shuttle_scout.config import Config


def _pt(kind, x, y, elev_m, road=50.0):
    return CandidatePoint(kind=kind, row=0, col=0, x=x, y=y,
                          elev_m=elev_m, road_dist_m=road)


@pytest.fixture
def cfg():
    return Config()


def test_pair_passes_all_gates(cfg):
    start = _pt("start", 0.0, 0.0, 2000.0)
    end = _pt("end", 3000.0, 0.0, 2000.0 - units.feet_to_meters(1200))
    pairs = screen_pairs([start], [end], cfg)
    assert len(pairs) == 1
    assert pairs[0].drop_ft == pytest.approx(1200.0, abs=0.1)
    assert pairs[0].avg_grade_pct == pytest.approx(12.19, abs=0.1)


def test_drop_gate(cfg):
    start = _pt("start", 0.0, 0.0, 2000.0)
    shallow = _pt("end", 3000.0, 0.0, 2000.0 - units.feet_to_meters(900))
    assert screen_pairs([start], [shallow], cfg) == []


def test_drop_gate_respects_lowered_minimum(cfg):
    cfg.hard.min_drop_ft = 500.0
    start = _pt("start", 0.0, 0.0, 2000.0)
    end = _pt("end", 3000.0, 0.0, 2000.0 - units.feet_to_meters(600))
    assert len(screen_pairs([start], [end], cfg)) == 1


def test_grade_gates(cfg):
    start = _pt("start", 0.0, 0.0, 2000.0)
    drop = units.feet_to_meters(1100)
    # Too shallow: 1100 ft over ~7 mi ≈ 3% < 6%.
    far = _pt("end", 11000.0, 0.0, 2000.0 - drop)
    assert screen_pairs([start], [far], cfg) == []
    # Too steep: > 100% grade over 500 m run with 550 m drop... use closer end.
    cfg2 = Config()
    cfg2.hard.min_pair_distance_mi = 0.1
    near = _pt("end", 450.0, 0.0, 2000.0 - units.feet_to_meters(1700))
    assert screen_pairs([start], [near], cfg2) == []


def test_distance_window(cfg):
    start = _pt("start", 0.0, 0.0, 2000.0)
    drop = units.feet_to_meters(1200)
    too_close = _pt("end", 200.0, 0.0, 2000.0 - drop)  # < 0.25 mi
    assert screen_pairs([start], [too_close], cfg) == []


def test_max_drop_cap_gate(cfg):
    cfg.hard.max_drop_ft = 1500.0
    start = _pt("start", 0.0, 0.0, 3000.0)
    huge = _pt("end", 3000.0, 0.0, 3000.0 - units.feet_to_meters(2000))
    ok = _pt("end", 3000.0, 100.0, 3000.0 - units.feet_to_meters(1200))
    pairs = screen_pairs([start], [huge, ok], cfg)
    assert len(pairs) == 1
    assert pairs[0].drop_ft == pytest.approx(1200.0, abs=0.1)
