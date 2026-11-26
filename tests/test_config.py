import json

import pytest

from shuttle_scout.config import Config


def test_defaults_valid():
    Config().validate()


def test_min_drop_floor_enforced():
    cfg = Config()
    cfg.hard.min_drop_ft = 250.0  # below the 300 ft floor
    with pytest.raises(ValueError, match="floor"):
        cfg.validate()


def test_min_drop_at_floor_ok():
    cfg = Config()
    cfg.hard.min_drop_ft = 300.0
    cfg.validate()


def test_400ft_drop_allowed():
    cfg = Config()
    cfg.hard.min_drop_ft = 400.0
    cfg.validate()


def test_weights_normalized_sum_to_one():
    w = Config().weights.normalized()
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["drop_efficiency"] > w["grade_consistency"]  # drop dominates


def test_unit_conversion_boundaries():
    cfg = Config()
    assert cfg.hard.min_drop_m == pytest.approx(304.8, abs=0.01)
    assert cfg.params.road_access_m == pytest.approx(402.336)
    assert cfg.hard.max_drive_time_s == pytest.approx(2700.0)


def test_from_json_overrides(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({
        "hard": {"min_drop_ft": 600.0, "max_drive_time_min": 30.0},
        "weights": {"drop_efficiency": 0.5},
        "params": {"cell_size_m": 30.0},
    }))
    cfg = Config.from_json(p)
    assert cfg.hard.min_drop_ft == 600.0
    assert cfg.hard.max_drive_time_min == 30.0
    assert cfg.weights.drop_efficiency == 0.5
    assert cfg.params.cell_size_m == 30.0


def test_from_json_rejects_unknown_key(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"hard": {"min_dorp_ft": 600.0}}))
    with pytest.raises(KeyError):
        Config.from_json(p)


def test_max_drop_cap_validation():
    cfg = Config()
    cfg.hard.max_drop_ft = 900.0  # below min_drop 1000
    with pytest.raises(ValueError, match="max_drop_ft"):
        cfg.validate()


def test_ramps_from_json(tmp_path):
    import json
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({
        "hard": {"min_drop_ft": 400.0, "max_drop_ft": 1000.0},
        "ramps": {"drop_full_credit_ft": 800.0, "drive_preferred_min": 8.0},
    }))
    cfg = Config.from_json(p)
    assert cfg.ramps.drop_full_credit_ft == 800.0
    assert cfg.ramps.drive_preferred_min == 8.0


def test_ramps_validation_rejects_inverted_drop():
    cfg = Config()
    cfg.ramps.drop_full_credit_ft = 500.0  # below min_drop 1000
    with pytest.raises(ValueError, match="drop_full_credit"):
        cfg.validate()


def test_new_gates_have_sane_defaults():
    cfg = Config()
    cfg.validate()
    assert cfg.hard.min_shuttle_drive_mi > 0
    assert cfg.hard.min_route_to_access_ratio >= 1.0
    assert cfg.hard.max_route_climb_ft == float("inf")


def test_dh_profile_loads():
    cfg = Config.from_json("profiles/dh_friendly.json")
    assert cfg.hard.max_route_climb_ft == 400.0
    assert cfg.ramps.climb_tolerance_ft == 120.0
