import numpy as np
import pytest

from shuttle_scout import units
from shuttle_scout.candidates import CandidatePair, CandidatePoint
from shuttle_scout.config import Config
from shuttle_scout.routing import Route
from shuttle_scout.scoring import rank, score_pairs


def _route(climb_m=0.0, grade_std=5.0):
    return Route(rows=np.array([0, 1]), cols=np.array([0, 1]),
                 length_m=3000.0, total_climb_m=climb_m,
                 grade_std_pct=grade_std, crosses_hazard=False)


def _pair(x_end=3000.0, drop_ft=1500.0, road_dist=50.0, drive_min=20.0,
          climb_m=0.0, grade_std=5.0, x_start=0.0):
    start = CandidatePoint("start", 0, 0, x_start, 0.0, 2500.0, road_dist)
    drop = units.feet_to_meters(drop_ft)
    end = CandidatePoint("end", 5, 5, x_end, 0.0, 2500.0 - drop, road_dist)
    run = abs(x_end - x_start)
    p = CandidatePair(start=start, end=end, drop_m=drop, run_m=run,
                      avg_grade_pct=units.grade_percent(drop, run))
    p.drive_time_s = drive_min * 60.0
    p.routes = [_route(climb_m, grade_std)]
    return p


def test_scores_bounded_and_complete():
    cfg = Config()
    pairs = score_pairs([_pair(), _pair(drop_ft=1100, drive_min=44)], cfg)
    for p in pairs:
        assert 0.0 <= p.score <= 1.0
        assert set(p.score_parts) == set(cfg.weights.normalized())
        for v in p.score_parts.values():
            assert 0.0 <= v <= 1.0


def test_bigger_drop_scores_higher():
    cfg = Config()
    small, big = _pair(drop_ft=1100), _pair(drop_ft=2500)
    ranked = rank([small, big], cfg)
    assert ranked[0] is big and ranked[0].rank == 1


def test_road_proximity_rewarded():
    cfg = Config()
    near, far = _pair(road_dist=10.0), _pair(road_dist=390.0)
    score_pairs([near, far], cfg)
    assert near.score_parts["access_proximity"] > far.score_parts["access_proximity"]


def test_intermediate_climb_penalized():
    cfg = Config()
    flat, climby = _pair(climb_m=0.0), _pair(climb_m=100.0)
    score_pairs([flat, climby], cfg)
    assert flat.score_parts["intermediate_climb"] > climby.score_parts["intermediate_climb"]


def test_dedup_suppresses_near_identical_pairs():
    cfg = Config()
    a = _pair(drop_ft=1500)
    b = _pair(drop_ft=1490)          # same locations, slightly worse
    c = _pair(x_start=5000.0, x_end=9000.0)  # far away
    ranked = rank([a, b, c], cfg)
    assert len(ranked) == 2
    assert b not in ranked


def test_ranked_list_truncates():
    cfg = Config()
    cfg.params.max_ranked_pairs = 1
    ranked = rank([_pair(), _pair(x_start=5000.0, x_end=9000.0)], cfg)
    assert len(ranked) == 1


def test_ramp_down_shape():
    from shuttle_scout.scoring import ramp_down
    assert ramp_down(100.0, 250.0, 400.0) == 1.0
    assert ramp_down(250.0, 250.0, 400.0) == 1.0
    assert ramp_down(325.0, 250.0, 400.0) == pytest.approx(0.5)
    assert ramp_down(500.0, 250.0, 400.0) == 0.0


def test_ramp_up_shape():
    from shuttle_scout.scoring import ramp_up
    assert ramp_up(300.0, 1000.0, 2500.0) == 0.0
    assert ramp_up(1750.0, 1000.0, 2500.0) == pytest.approx(0.5)
    assert ramp_up(3000.0, 1000.0, 2500.0) == 1.0


def test_drive_preferred_zone_scores_full():
    cfg = Config()
    quick = _pair(drive_min=10.0)   # under the 15-min preferred zone
    slow = _pair(drive_min=44.0)
    score_pairs([quick, slow], cfg)
    assert quick.score_parts["drivability"] == 1.0
    assert slow.score_parts["drivability"] < 0.1


def test_absolute_climb_ramp_dh_profile():
    cfg = Config()
    cfg.ramps.climb_tolerance_ft = 120.0
    from shuttle_scout import units as u
    zero = _pair(climb_m=0.0, drop_ft=2000)
    small = _pair(climb_m=u.feet_to_meters(60), drop_ft=2000)   # 60 ft
    over = _pair(climb_m=u.feet_to_meters(200), drop_ft=2000)   # 200 ft
    score_pairs([zero, small, over], cfg)
    assert zero.score_parts["intermediate_climb"] == 1.0
    assert 0.4 < small.score_parts["intermediate_climb"] < 0.6
    assert over.score_parts["intermediate_climb"] == 0.0
