"""Terrain analysis: slope, hazard masking, and distance-to-road surfaces.

All arrays are on the shared ``AnalysisGrid``; elevations in meters, slope
in percent, distances in meters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .config import Config
from .data_loading import Datasets

log = logging.getLogger(__name__)


@dataclass
class TerrainAnalysis:
    slope_pct: np.ndarray        # float32, percent slope per cell
    hazard: np.ndarray           # bool, absolute no-cross (lake/nodata/class)
    cliff: np.ndarray            # bool, extreme-cost cliff (crossed only if
                                 # no ride-around; such a route is rejected)
    stream: np.ndarray           # bool, buffered streams (crossable at cost)
    unrideable: np.ndarray       # bool, hazard OR too steep to ride
    drivable_roads: np.ndarray   # bool, road cells a shuttle can use
    steep_roads: np.ndarray      # bool, road cells over the grade limit
    road_distance_m: np.ndarray  # float32, distance to nearest DRIVABLE road


def compute_slope_percent(dem_m: np.ndarray, cell_size_m: float) -> np.ndarray:
    """Cell slope (percent) via central differences (Horn-style gradients).

    NaN cells propagate to NaN slope; callers must mask them.
    """
    dz_dy, dz_dx = np.gradient(dem_m, cell_size_m)
    slope = 100.0 * np.hypot(dz_dx, dz_dy)
    slope[np.isnan(dem_m)] = np.nan  # central differences skip the cell itself
    return slope.astype(np.float32)


def buffer_mask(mask: np.ndarray, buffer_m: float, cell_size_m: float) -> np.ndarray:
    """Grow a boolean mask outward by ``buffer_m`` (euclidean)."""
    if not mask.any() or buffer_m <= 0:
        return mask.copy()
    dist = ndimage.distance_transform_edt(~mask, sampling=cell_size_m)
    return dist <= buffer_m


def distance_to_mask_m(mask: np.ndarray, cell_size_m: float) -> np.ndarray:
    """Euclidean distance (meters) from every cell to the nearest True cell."""
    if not mask.any():
        return np.full(mask.shape, np.inf, dtype=np.float32)
    return ndimage.distance_transform_edt(~mask, sampling=cell_size_m).astype(
        np.float32
    )


def road_grade_estimate(
    dem_m: np.ndarray, roads: np.ndarray, window_m: float, cell_size_m: float
) -> np.ndarray:
    """Windowed along-road grade estimate (percent), NaN off-road.

    Grade = (max - min elevation among road cells in the window) divided by
    the window diameter. Exact for a straight road crossing the window;
    overestimates on tight switchbacks (path longer than the chord), which
    errs on the side of calling a road too steep. Documented trade-off —
    widen ``road_grade_window_m`` if switchbacked access roads get cut.
    """
    w = max(3, int(round(window_m / cell_size_m)) | 1)
    elev = np.where(roads, dem_m, np.nan)
    mx = ndimage.maximum_filter(np.where(np.isnan(elev), -np.inf, elev), size=w)
    mn = ndimage.minimum_filter(np.where(np.isnan(elev), np.inf, elev), size=w)
    grade = np.full(dem_m.shape, np.nan, dtype=np.float32)
    valid = roads & np.isfinite(mx) & np.isfinite(mn)
    grade[valid] = 100.0 * (mx - mn)[valid] / window_m
    return grade


def analyze(data: Datasets, cfg: Config) -> TerrainAnalysis:
    cell = data.grid.cell_size_m
    slope = compute_slope_percent(data.dem_m, cell)

    nodata = np.isnan(data.dem_m)
    # Cliffs are extreme-cost, NOT absolute hazards: routing may cross one
    # only if there is no ride-around, and such a route is then rejected.
    # Absolute hazards (lakes, river polygons, nodata) can never be crossed.
    cliff = slope >= cfg.hard.cliff_slope_pct
    bodies = buffer_mask(data.hydro_class == 2, cfg.hard.water_buffer_m, cell)
    stream = buffer_mask(data.hydro_class == 1, cfg.hard.water_buffer_m,
                         cell) & ~bodies

    hazard = bodies | nodata
    if data.terrain_class is not None:
        bad_classes = np.isin(
            data.terrain_class, cfg.params.unrideable_terrain_classes
        )
        hazard |= bad_classes
        log.info("Terrain classes excluded %d cells", int(bad_classes.sum()))

    too_steep = slope >= cfg.hard.max_rideable_slope_pct
    unrideable = hazard | too_steep | cliff

    road_grade = road_grade_estimate(
        data.dem_m, data.roads, cfg.params.road_grade_window_m, cell)
    with np.errstate(invalid="ignore"):
        steep_roads = data.roads & (road_grade > cfg.params.max_road_grade_pct)
    drivable_roads = data.roads & ~steep_roads
    if data.roads.any():
        log.info(
            "Road grade limit (windowed avg > %.0f%% over %.0f m): %d of %d "
            "road cells marked non-drivable",
            cfg.params.max_road_grade_pct, cfg.params.road_grade_window_m,
            int(steep_roads.sum()), int(data.roads.sum()),
        )
    road_distance = distance_to_mask_m(drivable_roads, cell)

    in_aoi = data.aoi_mask
    log.info(
        "Terrain: median slope %.1f%% | cliffs %.1f%% | water(+%.0fm buffer) "
        "%.1f%% | unrideable total %.1f%% of AOI",
        float(np.nanmedian(slope[in_aoi])),
        100 * (cliff & in_aoi).sum() / in_aoi.sum(),
        cfg.hard.water_buffer_m,
        100 * ((bodies | stream) & in_aoi).sum() / in_aoi.sum(),
        100 * (unrideable & in_aoi).sum() / in_aoi.sum(),
    )
    return TerrainAnalysis(
        slope_pct=slope,
        hazard=hazard,
        cliff=cliff,
        stream=stream,
        unrideable=unrideable,
        drivable_roads=drivable_roads,
        steep_roads=steep_roads,
        road_distance_m=road_distance,
    )
