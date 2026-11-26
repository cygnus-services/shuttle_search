"""Candidate start/end point generation and hard-constraint pair filtering.

Strategy
--------
1. Start candidates = local elevation MAXIMA; end candidates = local MINIMA.
   Both restricted to cells that are inside the AOI, not hazardous, and
   within the configured road-access distance (default 1/4 mile). Local
   extrema detection doubles as spatial non-max suppression, so candidates
   are naturally spread across the polygon.
2. Every start x end pair is screened against the *cheap* hard constraints
   (drop, horizontal separation, average grade). Drive time is expensive,
   so the routing module applies that gate afterwards, only to survivors.

Units: everything here is meters / percent on the shared analysis grid.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from . import units
from .config import Config
from .data_loading import Datasets
from .terrain import TerrainAnalysis

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidatePoint:
    kind: str  # "start" | "end"
    row: int
    col: int
    x: float
    y: float
    elev_m: float
    road_dist_m: float


@dataclass
class CandidatePair:
    start: CandidatePoint
    end: CandidatePoint
    drop_m: float
    run_m: float           # straight-line horizontal separation
    avg_grade_pct: float
    drive_time_s: float | None = None   # filled by routing.estimate_drive_time
    routes: list = field(default_factory=list)  # filled by routing.build_routes
    drive_path: tuple | None = None   # (rows, cols) traced shuttle drive
    score: float | None = None                  # filled by scoring.rank
    score_parts: dict = field(default_factory=dict)
    rank: int | None = None

    @property
    def drop_ft(self) -> float:
        return units.meters_to_feet(self.drop_m)


def _local_extrema(
    dem: np.ndarray, eligible: np.ndarray, window_cells: int, maxima: bool
) -> np.ndarray:
    """Boolean mask of eligible cells that are the max (or min) of their
    neighborhood *among eligible cells*.

    Ineligible/NaN cells are filled with -inf/+inf so they never win.
    Restricting the comparison to eligible cells matters: on monotone
    terrain the absolute local extremum is often an inaccessible summit or
    gully; what we want is the best *road-accessible* cell per neighborhood.
    """
    fill = -np.inf if maxima else np.inf
    z = np.where(eligible & ~np.isnan(dem), dem, fill)
    filt = ndimage.maximum_filter if maxima else ndimage.minimum_filter
    extreme = filt(z, size=window_cells, mode="nearest")
    return eligible & (z == extreme) & np.isfinite(z)


def _collect_points(
    kind: str,
    mask: np.ndarray,
    data: Datasets,
    terrain: TerrainAnalysis,
    limit: int,
    min_spacing_m: float = 0.0,
) -> list[CandidatePoint]:
    rows, cols = np.nonzero(mask)
    elevs = data.dem_m[rows, cols]
    # Best-first: highest starts / lowest ends maximize achievable drop.
    # min_spacing_m > 0 additionally enforces geographic spread (greedy):
    # a candidate too close to an already-selected one is skipped, so the
    # pool samples the whole polygon instead of stacking on one ridge —
    # essential when hunting short drops rather than maximum vertical.
    order = np.argsort(elevs)
    if kind == "start":
        order = order[::-1]
    pts: list[CandidatePoint] = []
    for i in order:
        if len(pts) >= limit:
            break
        r, c = int(rows[i]), int(cols[i])
        x, y = data.grid.xy_from_rowcol(r, c)
        if min_spacing_m > 0 and any(
            np.hypot(x - q.x, y - q.y) < min_spacing_m for q in pts
        ):
            continue
        pts.append(
            CandidatePoint(
                kind=kind, row=r, col=c, x=x, y=y,
                elev_m=float(data.dem_m[r, c]),
                road_dist_m=float(terrain.road_distance_m[r, c]),
            )
        )
    return pts


def generate_points(
    data: Datasets, terrain: TerrainAnalysis, cfg: Config
) -> tuple[list[CandidatePoint], list[CandidatePoint]]:
    """Candidate start (high) and end (low) points with road access."""
    p = cfg.params
    window_cells = max(3, int(round(p.local_extrema_window_m / data.grid.cell_size_m)))
    eligible = (
        data.aoi_mask
        & ~terrain.hazard
        & (terrain.road_distance_m <= p.road_access_m)
        & ~np.isnan(data.dem_m)
    )
    log.info(
        "Eligible candidate cells (in AOI, safe, <= %.2f mi from road): %d",
        p.road_access_mi, int(eligible.sum()),
    )

    starts = _collect_points(
        "start", _local_extrema(data.dem_m, eligible, window_cells, maxima=True),
        data, terrain, p.max_start_candidates, p.candidate_min_spacing_m,
    )
    ends = _collect_points(
        "end", _local_extrema(data.dem_m, eligible, window_cells, maxima=False),
        data, terrain, p.max_end_candidates, p.candidate_min_spacing_m,
    )
    n_valley_ends = len(ends)

    # Ridgeline descents end where the ridge meets a road — a mid-slope
    # pickup point, NOT a topographic valley minimum. Seed extra END
    # candidates directly on the drivable road network (thinned to
    # road_endpoint_spacing_m, lowest-first) so a road-accessible peak can
    # pair with any road below it. This is what surfaces "peak -> ridge ->
    # another road" candidates that pure valley-minima ends miss.
    n_peak_starts = len(starts)
    if p.road_endpoint_spacing_m > 0 and len(starts) < p.max_start_candidates:
        # Symmetric to ends: a start may sit on a road ANYWHERE high — a
        # ridge shoulder where a road tops out is a perfectly good drop-off
        # even though it is not a strict peak. Exclude local minima only
        # (nobody starts a descent in a hole).
        local_min_all = _local_extrema(
            data.dem_m, data.aoi_mask & ~np.isnan(data.dem_m),
            window_cells, maxima=False)
        backcountry = ((data.road_class >= 1)
                       & (data.road_class <= p.road_endpoint_max_class))
        road_start_seed = (terrain.drivable_roads & data.aoi_mask & backcountry
                           & ~np.isnan(data.dem_m) & ~local_min_all)
        for rs in _collect_points(
            "start", road_start_seed, data, terrain,
            p.max_start_candidates - len(starts), p.road_endpoint_spacing_m,
        ):
            if not any(np.hypot(rs.x - s.x, rs.y - s.y)
                       < p.road_endpoint_spacing_m for s in starts):
                starts.append(rs)
        log.info("Added %d road-access start candidates (%d peaks + %d road)",
                 len(starts) - n_peak_starts, n_peak_starts,
                 len(starts) - n_peak_starts)

    if p.road_endpoint_spacing_m > 0 and len(ends) < p.max_end_candidates:
        # Ends may sit on a road mid-slope (not a valley minimum) — but never
        # on a local maximum; nobody shuttles to end on a peak.
        local_max = _local_extrema(data.dem_m, data.aoi_mask & ~np.isnan(data.dem_m),
                                   window_cells, maxima=True)
        backcountry = ((data.road_class >= 1)
                       & (data.road_class <= p.road_endpoint_max_class))
        road_seed = (terrain.drivable_roads & data.aoi_mask & backcountry
                     & ~np.isnan(data.dem_m) & ~local_max)
        road_ends = _collect_points(
            "end", road_seed, data, terrain,
            p.max_end_candidates - len(ends), p.road_endpoint_spacing_m,
        )
        # Deduplicate against valley ends already chosen (same spacing rule).
        for re in road_ends:
            if not any(np.hypot(re.x - e.x, re.y - e.y)
                       < p.road_endpoint_spacing_m for e in ends):
                ends.append(re)
        log.info("Added %d road-access end candidates (%d valley + %d road)",
                 len(ends) - n_valley_ends, n_valley_ends,
                 len(ends) - n_valley_ends)
    log.info("Candidates: %d starts, %d ends", len(starts), len(ends))
    if starts:
        top = starts[0]
        log.info("Highest start: %.0f ft at (%.0f, %.0f)",
                 units.meters_to_feet(top.elev_m), top.x, top.y)
    return starts, ends


def screen_pairs(
    starts: list[CandidatePoint],
    ends: list[CandidatePoint],
    cfg: Config,
) -> list[CandidatePair]:
    """Apply the cheap hard constraints to every start x end combination.

    Gates applied here: minimum drop, horizontal separation window, and
    average grade window. Drive time and route feasibility are applied later
    by the routing module (they are orders of magnitude more expensive).
    """
    h = cfg.hard
    pairs: list[CandidatePair] = []
    n_drop = n_dist = n_grade = 0
    for s, e in itertools.product(starts, ends):
        drop = s.elev_m - e.elev_m
        if not (h.min_drop_m <= drop <= h.max_drop_m):
            n_drop += 1
            continue
        run = float(np.hypot(s.x - e.x, s.y - e.y))
        if not (h.min_pair_distance_m <= run <= h.max_pair_distance_m):
            n_dist += 1
            continue
        grade = units.grade_percent(drop, run)
        if not (h.min_avg_grade_pct <= grade <= h.max_avg_grade_pct):
            n_grade += 1
            continue
        pairs.append(
            CandidatePair(start=s, end=e, drop_m=drop, run_m=run,
                          avg_grade_pct=grade)
        )
    log.info(
        "Pair screening: %d combos -> %d pass (rejected: %d drop outside "
        "%.0f-%s ft, %d distance, %d grade outside %.0f-%.0f%%)",
        len(starts) * len(ends), len(pairs), n_drop, h.min_drop_ft,
        "inf" if np.isinf(h.max_drop_ft) else f"{h.max_drop_ft:.0f}",
        n_dist, n_grade, h.min_avg_grade_pct, h.max_avg_grade_pct,
    )
    # Deduplicate near-identical pairs (same start, ends very close, etc.)
    # is deferred to ranking; keeping the screen simple and transparent.
    return pairs
