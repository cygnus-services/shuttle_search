"""End-to-end orchestration: load -> analyze -> candidates -> routes ->
rank -> write. Each stage logs its inputs/outputs so a run is auditable."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from . import candidates, output, routing, scoring, terrain, zones as zones_mod
from .candidates import CandidatePair
from .config import Config
from .data_loading import load_datasets

log = logging.getLogger(__name__)


def run(
    polygon_path: str | Path,
    dem_path: str | Path,
    roads_path: str | Path,
    hydro_path: str | Path,
    out_dir: str | Path,
    cfg: Config | None = None,
    terrain_path: str | Path | None = None,
    dem_vertical_units: str = "meters",
    zones: bool = False,
) -> list[CandidatePair]:
    cfg = cfg or Config()
    cfg.validate()
    t0 = time.perf_counter()

    log.info("=== Stage 1/6: load & validate data ===")
    data = load_datasets(polygon_path, dem_path, roads_path, hydro_path,
                         cfg, terrain_path, dem_vertical_units)

    log.info("=== Stage 2/6: terrain analysis ===")
    ta = terrain.analyze(data, cfg)

    log.info("=== Stage 3/6: candidate points & pair screening ===")
    starts, ends = candidates.generate_points(data, ta, cfg)
    pairs = candidates.screen_pairs(starts, ends, cfg)

    log.info("=== Stage 4/6: drive-time gate & descent routes ===")
    pairs = routing.estimate_drive_times(pairs, data, ta, cfg)
    pairs = routing.build_routes(pairs, data, ta, cfg)

    log.info("=== Stage 5/6: scoring & ranking ===")
    ranked = scoring.rank(pairs, cfg)
    routing.build_drive_routes(ranked, data, ta, cfg)

    log.info("=== Stage 6/6: write outputs ===")
    tiff = output.write_all(out_dir, ranked, data, ta, cfg,
                            zones_mode=zones)
    output.write_topo(Path(out_dir), data,
                      Path(dem_path).parent / "usgs_topo.tif")
    if zones:
        scoring.score_pairs(pairs, cfg)   # score the full surviving pool
        conns = zones_mod.build_zones(pairs, cfg, data.grid.cell_size_m,
                                      data, ta)
        routing.build_drive_routes([c.best for c in conns], data, ta, cfg)
        output.write_zone_layers(Path(out_dir), conns, data)

    log.info("Done in %.1f s — %d ranked pairs -> %s",
             time.perf_counter() - t0, len(ranked), tiff)
    return ranked
