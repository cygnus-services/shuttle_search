"""Output generation: one multi-band GeoTIFF for QGIS, plus a ranked CSV
and GeoJSON (points + example routes) for tabular review and web maps.

GeoTIFF bands (uint16, nodata 0, CRS/grid = the analysis grid):

  1  start_points — 3x3 blobs at ranked start points, value = rank (1 best)
  2  end_points   — same for end points
  3  routes       — example route cells, value = rank of their pair
  4  roads        — type class 1-5 (jeep..highway), 6 = too steep to drive
  5  hazards      — 1 = hazard (water/cliff/nodata/bad class), 2 = too steep

In QGIS: Layer > Add Raster Layer, then use "Symbology > Paletted/Unique
values" per band, or load the GeoJSON for clickable attributes.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import numpy as np
import rasterio
import rasterio.features
import rasterio.warp
from rasterio.enums import ColorInterp
from scipy import ndimage

from . import qgis_styles, units
from .candidates import CandidatePair
from .config import Config
from .data_loading import Datasets
from .terrain import TerrainAnalysis

log = logging.getLogger(__name__)

BAND_NAMES = ("start_points", "end_points", "routes", "roads", "hazards")


def _burn_point(band: np.ndarray, row: int, col: int, value: int) -> None:
    r0, r1 = max(row - 1, 0), min(row + 2, band.shape[0])
    c0, c1 = max(col - 1, 0), min(col + 2, band.shape[1])
    region = band[r0:r1, c0:c1]
    region[(region == 0) | (region > value)] = value  # best rank wins


def build_bands(
    ranked: list[CandidatePair],
    data: Datasets,
    terrain: TerrainAnalysis,
) -> np.ndarray:
    shape = data.grid.shape
    stack = np.zeros((len(BAND_NAMES), *shape), dtype=np.uint16)
    starts, ends, routes, roads, hazards = stack

    for pr in ranked:
        _burn_point(starts, pr.start.row, pr.start.col, pr.rank)
        _burn_point(ends, pr.end.row, pr.end.col, pr.rank)
        for rt in pr.routes:
            cells = routes[rt.rows, rt.cols]
            routes[rt.rows, rt.cols] = np.where(
                (cells == 0) | (cells > pr.rank), pr.rank, cells
            )

    roads[data.roads] = data.road_class[data.roads]
    roads[terrain.steep_roads] = 6
    hazards[terrain.stream] = 3
    hazards[terrain.unrideable] = 2
    hazards[terrain.hazard] = 1  # hazard overrides plain "too steep"
    return stack


def write_geotiff(
    path: Path,
    stack: np.ndarray,
    data: Datasets,
) -> None:
    profile = {
        "driver": "GTiff",
        "height": stack.shape[1],
        "width": stack.shape[2],
        "count": stack.shape[0],
        "dtype": "uint16",
        "crs": data.grid.crs,
        "transform": data.grid.transform,
        "nodata": 0,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as dst:
        for i, name in enumerate(BAND_NAMES, start=1):
            dst.write(stack[i - 1], i)
            dst.set_band_description(i, name)
    log.info("Wrote %s (%d bands, %dx%d)", path, stack.shape[0],
             stack.shape[2], stack.shape[1])


def _pair_properties(pr: CandidatePair) -> dict:
    best = min(pr.routes, key=lambda r: r.total_climb_m)
    return {
        "rank": pr.rank,
        "score": round(pr.score, 4),
        "drop_ft": round(pr.drop_ft, 0),
        "avg_grade_pct": round(pr.avg_grade_pct, 1),
        "run_mi": round(units.meters_to_miles(pr.run_m), 2),
        "drive_time_min": round(pr.drive_time_s / 60.0, 1),
        "start_elev_ft": round(units.meters_to_feet(pr.start.elev_m), 0),
        "end_elev_ft": round(units.meters_to_feet(pr.end.elev_m), 0),
        "start_road_dist_ft": round(units.meters_to_feet(pr.start.road_dist_m), 0),
        "end_road_dist_ft": round(units.meters_to_feet(pr.end.road_dist_m), 0),
        "route_len_mi": round(units.meters_to_miles(best.length_m), 2),
        "route_climb_ft": round(best.total_climb_ft, 0),
        "route_grade_std_pct": round(best.grade_std_pct, 1),
        "n_routes": len(pr.routes),
        **{f"score_{k}": round(v, 3) for k, v in pr.score_parts.items()},
    }


def write_csv(path: Path, ranked: list[CandidatePair]) -> None:
    if not ranked:
        path.write_text("")
        return
    rows = [_pair_properties(pr) for pr in ranked]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %s (%d rows)", path, len(rows))


_MD_COLUMNS = [  # (csv/property key, table header)
    ("rank", "#"),
    ("score", "score"),
    ("drop_ft", "drop (ft)"),
    ("avg_grade_pct", "grade %"),
    ("run_mi", "run (mi)"),
    ("drive_time_min", "shuttle (min)"),
    ("route_len_mi", "route (mi)"),
    ("route_climb_ft", "climb (ft)"),
    ("start_road_dist_ft", "start→rd (ft)"),
    ("end_road_dist_ft", "end→rd (ft)"),
]


def write_markdown(path: Path, ranked: list[CandidatePair]) -> None:
    """Human-readable ranked table; renders as a clean table in any IDE's
    Markdown preview. The CSV keeps the full column set (score parts etc.)."""
    lines = [
        "# Shuttle Scout — ranked candidate pairs",
        "",
        "Hotter color = better rank (see color_legend.svg). "
        "Full detail incl. per-factor scores: ranked_pairs.csv",
        "",
        "| " + " | ".join(h for _, h in _MD_COLUMNS) + " |",
        "|" + "|".join("---:" for _ in _MD_COLUMNS) + "|",
    ]
    for pr in ranked:
        props = _pair_properties(pr)
        lines.append(
            "| " + " | ".join(f"{props[k]:g}" if isinstance(props[k], float)
                              else str(props[k]) for k, _ in _MD_COLUMNS) + " |"
        )
    path.write_text("\n".join(lines) + "\n")
    log.info("Wrote %s (%d rows)", path, len(ranked))


def write_geojsons(out: Path, ranked: list[CandidatePair],
                   data: Datasets, point_layers: bool = True) -> None:
    """Three WGS84 GeoJSON layers — separate files so QGIS can toggle and
    style them independently (matching .qml sidecars are written next to
    them): start_points, end_points, routes."""

    def to_wgs84(coords_xy: list[tuple[float, float]]):
        xs, ys = zip(*coords_xy)
        lons, lats = rasterio.warp.transform(data.grid.crs, "EPSG:4326", xs, ys)
        return [[round(lo, 6), round(la, 6)] for lo, la in zip(lons, lats)]

    # Multiple ranked pairs often share a physical start or end point.
    # Writing one stacked feature per pair makes QGIS's label engine drop
    # ALL the colliding labels, so aggregate: one feature per unique cell,
    # carrying the best pair's properties, colored by best rank, labeled
    # with every rank present ("#3 · #8 · #10").
    start_groups: dict = {}
    end_groups: dict = {}
    for pr in ranked:
        for groups, pt in ((start_groups, pr.start), (end_groups, pr.end)):
            groups.setdefault((pt.row, pt.col), []).append(pr)

    starts, ends, routes = [], [], []
    for feats, kind, groups in ((starts, "start", start_groups),
                                (ends, "end", end_groups)):
        for prs in groups.values():
            prs.sort(key=lambda p: p.rank)
            best = prs[0]
            pt = best.start if kind == "start" else best.end
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": to_wgs84([(pt.x, pt.y)])[0]},
                "properties": {
                    **_pair_properties(best),
                    "kind": kind,
                    "n_pairs_here": len(prs),
                    "ranks_label": " · ".join(f"#{p.rank}" for p in prs),
                },
            })

    for pr in ranked:
        props = _pair_properties(pr)
        for j, rt in enumerate(pr.routes, start=1):
            coords = [data.grid.xy_from_rowcol(int(r), int(c))
                      for r, c in zip(rt.rows[::3], rt.cols[::3])]
            coords.append(data.grid.xy_from_rowcol(int(rt.rows[-1]),
                                                   int(rt.cols[-1])))
            routes.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": to_wgs84(coords)},
                "properties": {**props, "kind": f"route_{j}"},
            })
    drives = []
    for pr in ranked:
        if pr.drive_path is None:
            continue
        rws, cls = pr.drive_path
        coords = [data.grid.xy_from_rowcol(int(r), int(c))
                  for r, c in zip(rws[::3], cls[::3])]
        coords.append(data.grid.xy_from_rowcol(int(rws[-1]), int(cls[-1])))
        drives.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": to_wgs84(coords)},
            "properties": {**_pair_properties(pr), "kind": "shuttle_drive"},
        })

    layers = [("start_points", starts), ("end_points", ends),
              ("routes", routes)] if point_layers else []
    layers.append(("drive_routes", drives))
    for name, feats in layers:
        path = out / f"{name}.geojson"
        path.write_text(json.dumps(
            {"type": "FeatureCollection", "features": feats}, indent=1))
        log.info("Wrote %s (%d features)", path, len(feats))


def write_overlays(out: Path, data: Datasets,
                   terrain: TerrainAnalysis) -> None:
    """Single-band uint8 rasters for independent on/off toggling in QGIS:
    roads_overlay (1 = road) and hazards_overlay (1 = hazard, 2 = steep)."""
    profile = {
        "driver": "GTiff", "height": data.grid.shape[0],
        "width": data.grid.shape[1], "count": 1, "dtype": "uint8",
        "crs": data.grid.crs, "transform": data.grid.transform,
        "nodata": 0, "compress": "deflate", "tiled": True,
    }
    # Roads: 1 = drivable, 2 = over the windowed grade limit. Dilated one
    # cell each side purely for visibility (the analysis rasters are not).
    # Type classes 1-5 (jeep..highway), 6 = over the drive-grade limit;
    # dilated one cell per side for visual line width.
    roads = data.road_class.copy()
    roads[terrain.steep_roads] = 6
    roads = ndimage.grey_dilation(roads, size=3)
    roads[~data.aoi_mask] = 0           # clip to the drawn polygon
    hazards = np.zeros(data.grid.shape, dtype=np.uint8)
    hazards[terrain.stream] = 3
    hazards[terrain.unrideable] = 2
    hazards[terrain.hazard] = 1
    hazards[~data.aoi_mask] = 0         # clip to the drawn polygon
    for name, arr in (("roads_overlay", roads), ("hazards_overlay", hazards)):
        path = out / f"{name}.tif"
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(arr, 1)
            dst.set_band_description(1, name)
        log.info("Wrote %s", path)


def write_elevation_ft(out: Path, data: Datasets) -> None:
    """DEM in FEET on the analysis grid, for the QGIS Value Tool's live
    elevation-under-cursor readout. Clipped to this polygon, so it ships
    small alongside the other draggable layers. A .qml sidecar renders it
    as a subtle semi-transparent hillshade-free grayscale you can leave on
    or toggle off."""
    elev_ft = np.where(np.isnan(data.dem_m), -9999.0,
                       data.dem_m * float(units.meters_to_feet(1.0))
                       ).astype(np.float32)
    elev_ft[~data.aoi_mask] = -9999.0   # clip to the drawn polygon
    path = out / "elevation_ft.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=data.grid.shape[0],
        width=data.grid.shape[1], count=1, dtype="float32",
        crs=data.grid.crs, transform=data.grid.transform,
        nodata=-9999.0, compress="deflate", tiled=True,
    ) as dst:
        dst.write(elev_ft, 1)
        dst.set_band_description(1, "elevation_ft")
    valid = elev_ft[elev_ft > -9998.0]
    vmin = float(valid.min()) if valid.size else 0.0
    vmax = float(valid.max()) if valid.size else 1.0
    qgis_styles.write_elevation_sidecar(path.with_suffix(".qml"), vmin, vmax)
    log.info("Wrote %s (%.0f-%.0f ft)", path, vmin, vmax)


def write_zone_layers(out: Path, conns: list, data: Datasets) -> None:
    """Zone polygons + one representative trail per zone connection."""
    import rasterio.warp as _warp

    def poly_wgs84(geom):
        return _warp.transform_geom(data.grid.crs, "EPSG:4326",
                                    geom.__geo_interface__)

    def line_wgs84(rows, cols):
        pts = [data.grid.xy_from_rowcol(int(r), int(c))
               for r, c in zip(rows[::3], cols[::3])]
        pts.append(data.grid.xy_from_rowcol(int(rows[-1]), int(cols[-1])))
        xs, ys = zip(*pts)
        lons, lats = _warp.transform(data.grid.crs, "EPSG:4326", xs, ys)
        return [[round(a, 6), round(b, 6)] for a, b in zip(lons, lats)]

    for kind in ("start", "end"):
        feats, seen = [], set()
        for c in conns:
            z = c.start_zone if kind == "start" else c.end_zone
            if (kind, z.index) in seen:
                continue
            seen.add((kind, z.index))
            # True elevation band across the whole zone, sampled from the
            # DEM — the candidate points inside it are often a single cell,
            # which collapsed the label to "5852-5852 ft".
            zmask = rasterio.features.geometry_mask(
                [z.polygon.__geo_interface__], out_shape=data.grid.shape,
                transform=data.grid.transform, invert=True)
            vals = data.dem_m[zmask & ~np.isnan(data.dem_m)]
            if vals.size:
                lo = units.meters_to_feet(float(vals.min()))
                hi = units.meters_to_feet(float(vals.max()))
            else:
                lo, hi = z.elev_range_ft
            label = (f"{lo:,.0f} ft" if hi - lo < 50
                     else f"{lo:,.0f}-{hi:,.0f} ft")
            feats.append({
                "type": "Feature", "geometry": poly_wgs84(z.polygon),
                "properties": {
                    **_pair_properties(c.best), "kind": f"{kind}_zone",
                    "zone_id": z.index, "n_points": len(z.points),
                    "elev_lo_ft": round(lo), "elev_hi_ft": round(hi),
                    "elev_label": label,
                    "n_collapsed": c.n_collapsed,
                },
            })
        path = out / f"{kind}_zones.geojson"
        path.write_text(json.dumps(
            {"type": "FeatureCollection", "features": feats}, indent=1))
        log.info("Wrote %s (%d zones)", path, len(feats))

    feats = []
    for c in conns:
        for variant, pr in [("primary", c.best)] + [
                ("distinct", x) for x in c.extra]:
            if not pr.routes:
                continue
            rt = pr.routes[0]
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": line_wgs84(rt.rows, rt.cols)},
                "properties": {**_pair_properties(pr), "kind": variant,
                               "n_collapsed": c.n_collapsed},
            })
    drives = []
    for c in conns:
        if c.best.drive_path is None:
            continue
        rws, cls = c.best.drive_path
        drives.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": line_wgs84(rws, cls)},
            "properties": {**_pair_properties(c.best), "kind": "shuttle_drive"},
        })
    dpath = out / "drive_routes.geojson"
    dpath.write_text(json.dumps(
        {"type": "FeatureCollection", "features": drives}, indent=1))
    log.info("Wrote %s (%d drives, from zone connections)", dpath, len(drives))
    write_used_roads(out, [c.best for c in conns], data)

    path = out / "zone_routes.geojson"
    path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, indent=1))
    log.info("Wrote %s (%d routes)", path, len(feats))
    qgis_styles.write_zone_sidecars(out, len(conns),
                                    [c.best.score for c in conns])


def write_used_roads(out: Path, ranked: list[CandidatePair],
                     data: Datasets) -> None:
    """Only the road segments the shuttle drives actually use, each labelled
    with which connections share it ("#1, #3, #7").

    The full road overlay shows every road in the polygon, which is most of
    the visual noise on the map. This shows just the roads you would drive,
    and how contested each one is: a segment carrying five connections is a
    key artery, one carrying a single connection is a spur.
    """
    from collections import defaultdict
    cell_ranks: dict = defaultdict(set)
    for pr in ranked:
        if pr.drive_path is None:
            continue
        for r, c in zip(*pr.drive_path):
            cell_ranks[(int(r), int(c))].add(pr.rank)

    def to_wgs84(cells):
        pts = [data.grid.xy_from_rowcol(r, c) for r, c in cells]
        xs, ys = zip(*pts)
        lons, lats = rasterio.warp.transform(data.grid.crs, "EPSG:4326", xs, ys)
        return [[round(a, 6), round(b, 6)] for a, b in zip(lons, lats)]

    feats, emitted = [], set()

    def flush(run, sig):
        if len(run) < 2 or not sig:
            return
        if all(cell in emitted for cell in run):
            return          # this stretch already drawn by an earlier route
        emitted.update(run)
        cells = run[::2] + [run[-1]]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": to_wgs84(cells)},
            "properties": {
                "ranks": ", ".join(f"#{r}" for r in sig),
                "n_uses": len(sig), "primary_rank": sig[0],
            },
        })

    for pr in ranked:
        if pr.drive_path is None:
            continue
        run, run_sig = [], None
        for r, c in zip(*pr.drive_path):
            cell = (int(r), int(c))
            sig = tuple(sorted(cell_ranks[cell]))
            if sig != run_sig:
                flush(run, run_sig)
                run, run_sig = [], sig
            run.append(cell)
        flush(run, run_sig)

    path = out / "used_roads.geojson"
    path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, indent=1))
    qgis_styles.write_used_roads_sidecar(path.with_suffix(".qml"))
    log.info("Wrote %s (%d segments, %d road cells used)",
             path, len(feats), len(cell_ranks))


def write_topo(out: Path, data: Datasets, source: Path) -> None:
    """Clip the area's USGS topo raster onto this run's grid, so the topo
    ships in the output folder with everything else you drag into QGIS."""
    if not Path(source).exists():
        log.info("No topo at %s — skipping (scripts/make_topo_tif.py makes it)",
                 source)
        return
    from rasterio.enums import Resampling
    dst = np.zeros((3, *data.grid.shape), dtype=np.uint8)
    with rasterio.open(source) as src:
        for i in range(3):
            rasterio.warp.reproject(
                source=rasterio.band(src, i + 1), destination=dst[i],
                dst_transform=data.grid.transform, dst_crs=data.grid.crs,
                resampling=Resampling.bilinear)
    path = out / "usgs_topo.tif"
    alpha = (data.aoi_mask * 255).astype(np.uint8)  # transparent outside AOI
    with rasterio.open(
        path, "w", driver="GTiff", height=data.grid.shape[0],
        width=data.grid.shape[1], count=4, dtype="uint8",
        crs=data.grid.crs, transform=data.grid.transform,
        compress="deflate",
    ) as d:
        d.write(dst, indexes=[1, 2, 3])
        d.write(alpha, 4)
        d.colorinterp = [ColorInterp.red, ColorInterp.green,
                         ColorInterp.blue, ColorInterp.alpha]
    log.info("Wrote %s", path)


def write_all(
    out_dir: str | Path,
    ranked: list[CandidatePair],
    data: Datasets,
    terrain: TerrainAnalysis,
    cfg: Config,
    zones_mode: bool = False,
) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stack = build_bands(ranked, data, terrain)
    tiff = out / "shuttle_candidates.tif"
    write_geotiff(tiff, stack, data)
    write_csv(out / "ranked_pairs.csv", ranked)
    write_markdown(out / "ranked_pairs.md", ranked)
    write_geojsons(out, ranked, data, point_layers=not zones_mode)
    write_overlays(out, data, terrain)
    write_elevation_ft(out, data)
    write_used_roads(out, ranked, data)
    qgis_styles.write_sidecars(out, n_ranks=len(ranked),
                               scores=[pr.score for pr in ranked])
    if zones_mode:
        for orphan in ("start_points.qml", "end_points.qml", "routes.qml"):
            (out / orphan).unlink(missing_ok=True)
    qgis_styles.write_color_legend(out, [
        {"rank": pr.rank, "score": pr.score, "drop_ft": pr.drop_ft,
         "drive_time_min": pr.drive_time_s / 60.0}
        for pr in ranked
    ])
    return tiff
