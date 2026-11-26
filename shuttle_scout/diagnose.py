"""Why-are-there-no-candidates-HERE diagnostics.

Given the normal pipeline inputs plus a small *focus* polygon, walks the
elimination funnel for that area and reports, stage by stage, where its
would-be candidates die:

    terrain eligibility -> extrema -> pool selection -> pair screening
    -> drive-time gate -> route viability

Usage:
    python -m shuttle_scout.diagnose \
        --polygon examples/philipsburg_search_area.geojson \
        --dem ... --roads ... --hydro ... [--config ...] \
        --focus examples/echo_lake_focus.geojson

The report is plain logging; nothing is written unless --dump-masks is
given, which writes a small diagnostic GeoTIFF of the focus area's
eligibility masks for QGIS.
"""

from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import rasterio
import rasterio.features

from . import candidates, routing, terrain as terrain_mod, units
from .candidates import _local_extrema  # deliberate: diagnostics peek inside
from .config import Config
from .data_loading import load_datasets, load_polygon

log = logging.getLogger("shuttle_scout.diagnose")


def _pct(part: int | float, whole: int | float) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def diagnose(data, ta, cfg: Config, focus_path: str,
             dump_masks: str | None = None) -> None:
    p = cfg.params
    h = cfg.hard
    geom = load_polygon(focus_path, cfg.params.target_crs)
    focus = rasterio.features.geometry_mask(
        [geom.__geo_interface__], out_shape=data.grid.shape,
        transform=data.grid.transform, invert=True,
    ) & data.aoi_mask
    n_focus = int(focus.sum())
    if n_focus == 0:
        log.error("Focus polygon contains no AOI cells — is it inside the "
                  "search polygon and in WGS84?")
        return
    cell_mi2 = (data.grid.cell_size_m / 1609.344) ** 2
    log.info("=== FOCUS AREA: %d cells (%.2f sq mi) ===",
             n_focus, n_focus * cell_mi2)

    # --- 1. Terrain -------------------------------------------------------
    dem = data.dem_m
    lo = float(np.nanmin(dem[focus]))
    hi = float(np.nanmax(dem[focus]))
    relief_ft = units.meters_to_feet(hi - lo)
    log.info("[terrain] elevation %.0f-%.0f ft, relief %.0f ft (min drop "
             "needed: %.0f ft) %s",
             units.meters_to_feet(lo), units.meters_to_feet(hi), relief_ft,
             h.min_drop_ft,
             "" if relief_ft >= h.min_drop_ft else
             "<-- NOT ENOUGH RELIEF inside focus; pairs must reach outside")
    water = terrain_mod.buffer_mask(data.hydro, h.water_buffer_m,
                                    data.grid.cell_size_m)
    for name, mask in (
        ("water (+%.0f ft buffer)" % h.water_buffer_ft, water),
        ("hazard (water/cliff/nodata/class)", ta.hazard),
        ("unrideable (hazard or too steep)", ta.unrideable),
    ):
        log.info("[terrain] %s: %s of focus", name,
                 _pct(int((mask & focus).sum()), n_focus))

    # --- 2. Roads ---------------------------------------------------------
    roads_f = int((data.roads & focus).sum())
    drivable_f = int((ta.drivable_roads & focus).sum())
    steep_f = int((ta.steep_roads & focus).sum())
    within = int(((ta.road_distance_m <= p.road_access_m) & focus).sum())
    log.info("[roads] road cells in focus: %d (drivable %d, too-steep %d "
             "= %s of them)", roads_f, drivable_f, steep_f,
             _pct(steep_f, roads_f))
    log.info("[roads] focus cells within %.2f mi of a DRIVABLE road: %s",
             p.road_access_mi, _pct(within, n_focus))
    if roads_f and drivable_f == 0:
        log.warning("[roads] every road in focus exceeds the %.0f%% windowed "
                    "grade limit -> no access credit anywhere here",
                    p.max_road_grade_pct)

    # --- 3. Candidate eligibility & extrema ------------------------------
    eligible = (data.aoi_mask & ~ta.hazard
                & (ta.road_distance_m <= p.road_access_m)
                & ~np.isnan(dem))
    elig_f = int((eligible & focus).sum())
    log.info("[eligibility] eligible candidate cells in focus: %d (%s)",
             elig_f, _pct(elig_f, n_focus))

    window_cells = max(3, int(round(p.local_extrema_window_m
                                    / data.grid.cell_size_m)))
    hi_x = _local_extrema(dem, eligible, window_cells, maxima=True)
    lo_x = _local_extrema(dem, eligible, window_cells, maxima=False)
    n_hi_f = int((hi_x & focus).sum())
    n_lo_f = int((lo_x & focus).sum())
    log.info("[extrema] local start-extrema in focus: %d | end-extrema: %d "
             "(pre-pool)", n_hi_f, n_lo_f)

    starts_pool, ends_pool = candidates.generate_points(data, ta, cfg)
    s_in = [pt for pt in starts_pool if focus[pt.row, pt.col]]
    e_in = [pt for pt in ends_pool if focus[pt.row, pt.col]]
    log.info("[pool] focus candidates that made the global pool: %d starts "
             "(of %d pooled), %d ends (of %d pooled)",
             len(s_in), len(starts_pool), len(e_in), len(ends_pool))
    if n_hi_f and not s_in:
        log.warning("[pool] start-extrema exist here but were OUTCOMPETED — "
                    "raise params.max_start_candidates or lower "
                    "candidate_min_spacing_m")
    if n_lo_f and not e_in:
        log.warning("[pool] end-extrema exist here but were OUTCOMPETED — "
                    "raise params.max_end_candidates")

    # --- 4. Pair gates ----------------------------------------------------
    if not (s_in or e_in):
        log.info("[pairs] no pooled candidates in focus -> nothing to pair; "
                 "see stages above for the reason")
        return
    pairs = (candidates.screen_pairs(s_in, ends_pool, cfg)
             + [pr for pr in candidates.screen_pairs(starts_pool, e_in, cfg)
                if not focus[pr.start.row, pr.start.col]])
    log.info("[pairs] pairs touching focus that pass cheap gates: %d "
             "(rejection counts logged above by screen_pairs)", len(pairs))
    if not pairs:
        return

    pairs = routing.estimate_drive_times(pairs, data, ta, cfg)
    log.info("[drive] pairs surviving the %.0f-min drive gate: %d",
             h.max_drive_time_min, len(pairs))
    if not pairs:
        return
    pairs = routing.build_routes(pairs[:40], data, ta, cfg)
    log.info("[routes] pairs with a viable hazard-free descent route: %d",
             len(pairs))
    if pairs:
        best = max(pairs, key=lambda pr: pr.drop_m)
        log.info(
            "[verdict] focus CAN produce candidates (best local pair: "
            "%.0f ft drop, %.1f mi run, %.0f min shuttle). If absent from "
            "the top-N, it was outranked or deduplicated — compare against "
            "ranked_pairs.csv scores.",
            best.drop_ft, units.meters_to_miles(best.run_m),
            (best.drive_time_s or 0) / 60.0,
        )

    if dump_masks:
        stack = np.zeros((4, *data.grid.shape), dtype=np.uint8)
        stack[0][eligible & focus] = 1
        stack[1][(hi_x | lo_x) & focus] = 1
        stack[2][ta.steep_roads & focus] = 1
        stack[3][ta.hazard & focus] = 1
        profile = {
            "driver": "GTiff", "height": stack.shape[1],
            "width": stack.shape[2], "count": 4, "dtype": "uint8",
            "crs": data.grid.crs, "transform": data.grid.transform,
            "nodata": 0, "compress": "deflate",
        }
        with rasterio.open(dump_masks, "w", **profile) as dst:
            for i, name in enumerate(
                ("eligible", "extrema", "steep_roads", "hazard"), start=1
            ):
                dst.write(stack[i - 1], i)
                dst.set_band_description(i, name)
        log.info("[dump] wrote focus masks -> %s", dump_masks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--polygon", required=True)
    ap.add_argument("--dem", required=True)
    ap.add_argument("--roads", required=True)
    ap.add_argument("--hydro", required=True)
    ap.add_argument("--terrain", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--focus", required=True,
                    help="Small GeoJSON polygon to explain (WGS84)")
    ap.add_argument("--dump-masks", default=None,
                    help="Optional path for a focus-mask debug GeoTIFF")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-7s %(message)s")

    cfg = Config.from_json(args.config) if args.config else Config()
    cfg.validate()
    data = load_datasets(args.polygon, args.dem, args.roads, args.hydro,
                         cfg, args.terrain)
    ta = terrain_mod.analyze(data, cfg)
    diagnose(data, ta, cfg, args.focus, args.dump_masks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
