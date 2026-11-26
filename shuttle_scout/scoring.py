"""Soft scoring and ranking of pairs that survived every hard constraint.

Each factor is mapped to [0, 1] (1 = best) with a documented, self-scaling
formula, then combined with the normalized ``ScoringWeights``. Factors:

* drop_efficiency    — extra vertical beyond the required minimum, min-max
                       normalized within the cohort (relative ranking).
* access_proximity   — how close start AND end are to a road, relative to
                       the 1/4-mile access limit (0 at the limit, 1 on-road).
* intermediate_climb — climbing along the best route relative to the total
                       drop; a route that climbs 25% of its drop scores 0.
* grade_consistency  — per-segment grade std-dev of the best route; std of
                       0% scores 1, std >= 30% scores 0.
* drivability        — shuttle drive time relative to the 45-minute cap.

Near-duplicate suppression: ranked greedily, a pair whose start AND end
both fall within ``dedup_radius`` of an already-accepted pair is skipped,
so the top-N spreads across genuinely distinct terrain.
"""

from __future__ import annotations

import logging

import numpy as np

from .candidates import CandidatePair
from .config import Config

log = logging.getLogger(__name__)

def ramp_down(value: float, good: float, bad: float) -> float:
    """Piecewise-linear preference: 1.0 at/below ``good``, 0.0 at/above
    ``bad``, linear between. The building block of every scoring ramp."""
    if value <= good:
        return 1.0
    if value >= bad or bad <= good:
        return 0.0
    return 1.0 - (value - good) / (bad - good)


def ramp_up(value: float, bad: float, good: float) -> float:
    """1.0 at/above ``good``, 0.0 at/below ``bad`` — for more-is-better."""
    if value >= good:
        return 1.0
    if value <= bad or good <= bad:
        return 0.0
    return (value - bad) / (good - bad)


def score_pairs(pairs: list[CandidatePair], cfg: Config) -> list[CandidatePair]:
    if not pairs:
        return []
    w = cfg.weights.normalized()
    h = cfg.hard
    p = cfg.params
    r = cfg.ramps

    for pr in pairs:
        best = min(pr.routes, key=lambda rt: rt.total_climb_m)

        drop_eff = ramp_up(pr.drop_ft, h.min_drop_ft, r.drop_full_credit_ft)

        access = 0.5 * (
            ramp_down(pr.start.road_dist_m, r.access_preferred_m,
                      p.road_access_m)
            + ramp_down(pr.end.road_dist_m, r.access_preferred_m,
                        p.road_access_m)
        )

        climb_frac = best.total_climb_m / max(pr.drop_m, 1e-9)
        climb = ramp_down(climb_frac, 0.0, r.climb_tolerance_fraction)
        if r.climb_tolerance_ft != float("inf"):
            climb = min(climb, ramp_down(best.total_climb_ft, 0.0,
                                         r.climb_tolerance_ft))

        consistency = ramp_down(best.grade_std_pct, 0.0,
                                r.grade_std_ceiling_pct)

        assert pr.drive_time_s is not None, "drive-time gate must run first"
        drivability = ramp_down(pr.drive_time_s / 60.0,
                                r.drive_preferred_min, h.max_drive_time_min)

        pr.score_parts = {
            "drop_efficiency": float(drop_eff),
            "access_proximity": float(access),
            "intermediate_climb": float(climb),
            "grade_consistency": float(consistency),
            "drivability": float(drivability),
        }
        pr.score = float(sum(w[k] * v for k, v in pr.score_parts.items()))
    return pairs


def rank(pairs: list[CandidatePair], cfg: Config) -> list[CandidatePair]:
    """Sort by score, suppress near-duplicates, keep the top N."""
    pairs = score_pairs(pairs, cfg)
    pairs.sort(key=lambda pr: pr.score, reverse=True)

    dedup_radius = cfg.params.local_extrema_window_m
    selected: list[CandidatePair] = []
    for pr in pairs:
        dup = any(
            np.hypot(pr.start.x - s.start.x, pr.start.y - s.start.y) < dedup_radius
            and np.hypot(pr.end.x - s.end.x, pr.end.y - s.end.y) < dedup_radius
            for s in selected
        )
        if dup:
            continue
        selected.append(pr)
        if len(selected) >= cfg.params.max_ranked_pairs:
            break

    for i, pr in enumerate(selected, start=1):
        pr.rank = i
    if selected:
        best = selected[0]
        log.info(
            "Ranked %d pairs (from %d scored). #1: drop %.0f ft, avg grade "
            "%.0f%%, drive %.0f min, score %.3f",
            len(selected), len(pairs), best.drop_ft, best.avg_grade_pct,
            (best.drive_time_s or 0) / 60.0, best.score,
        )
    return selected
