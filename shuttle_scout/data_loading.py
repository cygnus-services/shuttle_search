"""Data loading, validation, and grid unification.

Inputs (all paths supplied by the user):

* ``polygon``  — GeoJSON file with one Polygon/MultiPolygon: the search area.
* ``dem``      — GeoTIFF of elevation. Assumed METERS unless told otherwise
                 (USGS 3DEP products are meters); converted to meters on load.
* ``roads``    — GeoTIFF where nonzero cells are drivable road (rasterized
                 OSM + Forest Service roads; see docs/DATA_SOURCING.md).
* ``hydro``    — GeoTIFF where nonzero cells are rivers/streams/waterbodies.
* ``terrain``  — optional GeoTIFF of land-cover classes (e.g. NLCD).

Everything is warped onto a single analysis grid: the search polygon's
bounding box (padded) in ``Config.params.target_crs`` at
``Config.params.cell_size_m`` resolution. Downstream modules only ever see
that one grid, which is what keeps row/col <-> x/y math trivial.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import rasterio.features
import rasterio.warp
from rasterio.enums import Resampling
from rasterio.transform import Affine, rowcol, xy
from shapely.geometry import shape
from shapely.ops import unary_union

from . import units
from .config import Config

log = logging.getLogger(__name__)

NODATA = np.float32(-9999.0)


@dataclass
class AnalysisGrid:
    """The single common grid all rasters are warped onto."""

    crs: str
    transform: Affine
    shape: tuple[int, int]  # (rows, cols)

    @property
    def cell_size_m(self) -> float:
        return abs(self.transform.a)

    def rowcol_from_xy(self, x: float, y: float) -> tuple[int, int]:
        r, c = rowcol(self.transform, x, y)
        return int(r), int(c)

    def xy_from_rowcol(self, row: int, col: int) -> tuple[float, float]:
        return xy(self.transform, row, col)


@dataclass
class Datasets:
    grid: AnalysisGrid
    dem_m: np.ndarray          # float32 elevation in METERS, NaN outside data
    roads: np.ndarray          # bool, True on road cells
    road_class: np.ndarray     # uint8 type: 0 none, 1 jeep, 2 dirt,
                               # 3 gravel, 4 paved, 5 highway
                               # (binary road rasters read as all 3)
    hydro: np.ndarray          # bool, True on any water cell
    hydro_class: np.ndarray    # uint8, 1 = stream, 2 = waterbody
    terrain_class: np.ndarray | None  # int32 land-cover codes, or None
    aoi_mask: np.ndarray       # bool, True inside the search polygon


def load_polygon(path: str | Path, target_crs: str):
    """Read a GeoJSON polygon file and return a shapely geometry in target_crs."""
    raw = json.loads(Path(path).read_text())
    if raw.get("type") == "FeatureCollection":
        geoms = [f["geometry"] for f in raw["features"]]
    elif raw.get("type") == "Feature":
        geoms = [raw["geometry"]]
    else:
        geoms = [raw]
    src_crs = "EPSG:4326"  # GeoJSON spec (RFC 7946) mandates WGS84
    projected = [
        rasterio.warp.transform_geom(src_crs, target_crs, g) for g in geoms
    ]
    geom = unary_union([shape(g) for g in projected])
    if geom.is_empty or geom.area <= 0:
        raise ValueError(f"search polygon in {path} is empty or degenerate")
    log.info(
        "Search polygon: %.1f sq mi (%s)",
        units.meters_to_miles(1) ** 2 * geom.area,
        target_crs,
    )
    return geom


def build_grid(polygon_geom, cfg: Config, pad_cells: int = 20) -> AnalysisGrid:
    """Analysis grid covering the polygon bounds plus padding.

    Padding lets distance-to-road and routing operate a little beyond the
    polygon edge so results at the boundary aren't artificially clipped.
    """
    cell = cfg.params.cell_size_m
    minx, miny, maxx, maxy = polygon_geom.bounds
    minx -= pad_cells * cell
    miny -= pad_cells * cell
    maxx += pad_cells * cell
    maxy += pad_cells * cell
    cols = int(np.ceil((maxx - minx) / cell))
    rows = int(np.ceil((maxy - miny) / cell))
    transform = Affine(cell, 0.0, minx, 0.0, -cell, maxy)
    n_mb = rows * cols * 4 / 1e6
    log.info("Analysis grid: %d rows x %d cols @ %.0f m (%.0f MB/band)",
             rows, cols, cell, n_mb)
    if rows * cols > 250_000_000:
        raise ValueError(
            "Analysis grid would exceed 250M cells; shrink the polygon or "
            "increase cell_size_m"
        )
    return AnalysisGrid(crs=cfg.params.target_crs, transform=transform,
                        shape=(rows, cols))


def _warp_to_grid(
    path: str | Path,
    grid: AnalysisGrid,
    resampling: Resampling,
    dtype=np.float32,
) -> np.ndarray:
    """Reproject band 1 of a GeoTIFF onto the analysis grid."""
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError(f"{path} has no CRS; assign one before use")
        dst = np.full(grid.shape, NODATA, dtype=np.float32)
        rasterio.warp.reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=NODATA,
            resampling=resampling,
        )
        log.info(
            "Loaded %s: src crs=%s res=%.4g, resampling=%s",
            Path(path).name, src.crs, src.res[0], resampling.name,
        )
    return dst.astype(dtype, copy=False)


def load_datasets(
    polygon_path: str | Path,
    dem_path: str | Path,
    roads_path: str | Path,
    hydro_path: str | Path,
    cfg: Config,
    terrain_path: str | Path | None = None,
    dem_vertical_units: str = "meters",
) -> Datasets:
    """Load, reproject, and validate all inputs onto one analysis grid."""
    geom = load_polygon(polygon_path, cfg.params.target_crs)
    grid = build_grid(geom, cfg)

    dem = _warp_to_grid(dem_path, grid, Resampling.bilinear)
    dem[dem == NODATA] = np.nan
    if dem_vertical_units == "feet":
        dem = np.float32(units.feet_to_meters(1.0)) * dem
        log.info("DEM converted from feet to meters")
    elif dem_vertical_units != "meters":
        raise ValueError("dem_vertical_units must be 'meters' or 'feet'")

    # Roads: nearest resampling; values are type classes 1-5 (see
    # scripts/prepare_data.py). A plain binary raster (all 1s from older
    # prep runs or third-party sources) is promoted to class 3 ("gravel")
    # so it neither dominates nor cripples the drive model.
    roads_raw = _warp_to_grid(roads_path, grid, Resampling.nearest)
    roads = (roads_raw != NODATA) & (roads_raw != 0) & ~np.isnan(roads_raw)
    road_class = np.zeros(grid.shape, dtype=np.uint8)
    road_class[roads] = np.clip(roads_raw[roads], 1, 5).astype(np.uint8)
    if bool(roads.any()) and int(roads_raw[roads].max()) <= 1:
        road_class[roads] = 3
        log.info("Road raster is binary — treating all roads as class 3 "
                 "(gravel)")
    # Hydro: class 1 = stream (crossable at a cost), 2 = waterbody
    # (impassable). A binary raster reads as all-streams.
    hydro_raw = _warp_to_grid(hydro_path, grid, Resampling.nearest)
    hydro = (hydro_raw != NODATA) & (hydro_raw != 0) & ~np.isnan(hydro_raw)
    hydro_class = np.zeros(grid.shape, dtype=np.uint8)
    hydro_class[hydro] = np.clip(hydro_raw[hydro], 1, 2).astype(np.uint8)

    terrain = None
    if terrain_path is not None:
        t = _warp_to_grid(terrain_path, grid, Resampling.nearest)
        t[t == NODATA] = 0
        terrain = t.astype(np.int32)

    aoi_mask = rasterio.features.geometry_mask(
        [geom.__geo_interface__],
        out_shape=grid.shape,
        transform=grid.transform,
        invert=True,
    )

    _validate(dem, roads, hydro, aoi_mask, grid)
    return Datasets(grid=grid, dem_m=dem, roads=roads,
                    road_class=road_class, hydro=hydro,
                    hydro_class=hydro_class,
                    terrain_class=terrain, aoi_mask=aoi_mask)


def _validate(dem, roads, hydro, aoi_mask, grid: AnalysisGrid) -> None:
    """Sanity checks with loud logging; hard-fail only when unusable."""
    inside = aoi_mask & ~np.isnan(dem)
    if not inside.any():
        raise ValueError("DEM has no valid data inside the search polygon")
    coverage = inside.sum() / max(aoi_mask.sum(), 1)
    if coverage < 0.9:
        log.warning("DEM covers only %.0f%% of the search polygon", 100 * coverage)

    lo = float(np.nanmin(dem[aoi_mask]))
    hi = float(np.nanmax(dem[aoi_mask]))
    log.info(
        "Elevation in AOI: %.0f-%.0f m (%.0f-%.0f ft), relief %.0f ft",
        lo, hi, units.meters_to_feet(lo), units.meters_to_feet(hi),
        units.meters_to_feet(hi - lo),
    )
    # Values far outside CONUS terrain limits suggest the DEM is in feet
    # (or broken) and the run would silently miscompute grades.
    if hi > 4500 or lo < -100:
        log.warning(
            "Elevations outside plausible CONUS range in meters — check "
            "dem_vertical_units (values look like feet?)"
        )

    if not (roads & aoi_mask).any():
        log.warning("No road cells inside the search polygon — every candidate "
                    "will fail the road-access constraint")
    log.info(
        "AOI cells: %d | road cells in AOI: %d | water cells in AOI: %d",
        int(aoi_mask.sum()), int((roads & aoi_mask).sum()),
        int((hydro & aoi_mask).sum()),
    )
