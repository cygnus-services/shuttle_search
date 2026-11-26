"""Turn downloaded vector/raster sources into the rasters shuttle_scout eats.

Reprojects everything to EPSG:26912 on a 10 m grid covering the search
polygon (plus margin), then writes into --out:

    dem.tif    float32 meters (clipped/warped 3DEP DEM)
    roads.tif  uint8, 1 = road (OSM drivable classes UNION USFS roads)
    hydro.tif  uint8, 1 = water (NHD flowlines + areas + waterbodies)

See docs/DATA_SOURCING.md for where each input comes from.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import rasterio
import rasterio.features
import rasterio.warp
from rasterio.enums import Resampling
from rasterio.transform import Affine
from shapely.geometry import box, shape
from shapely.ops import unary_union

TARGET_CRS = "EPSG:26912"  # default; override with --crs
CELL_M = 10.0
MARGIN_M = 2000.0  # keep context beyond the polygon for routing/drive time

# Road TYPE classes burned into roads.tif (higher = faster; the drive
# model assigns each class its own speed):
#   5 = highway     (OSM motorway/trunk/primary/secondary)
#   4 = paved local (OSM tertiary/residential; USFS asphalt/chip-seal)
#   3 = gravel      (USFS OML 3-5 or aggregate; OSM unclassified/service)
#   2 = dirt        (USFS OML 2 high-clearance; OSM track grades 1-3)
#   1 = jeep/barely (USFS OML 1, closed/custodial, unknown; track grade 4-5)
ROAD_CLASSES = (1, 2, 3, 4, 5)
OSM_CLASS = {
    "motorway": 5, "trunk": 5, "primary": 5, "secondary": 5,
    "motorway_link": 5, "trunk_link": 5, "primary_link": 5,
    "secondary_link": 5,
    "tertiary": 4, "tertiary_link": 4, "residential": 4,
    "unclassified": 3, "service": 3,
    "track": 2, "track_grade1": 2, "track_grade2": 2, "track_grade3": 2,
    "track_grade4": 1, "track_grade5": 1,
}


def usfs_class(props) -> int:
    surface = str(props.get("surface_type") or "")
    oml = str(props.get("oper_maint_level") or "")
    if surface[:2] in ("AC", "BS"):   # asphalt / bituminous surface
        return 4
    if oml[:1] in ("3", "4", "5") or surface[:3] == "AGG":
        return 3
    if oml[:1] == "2":
        return 2
    return 1  # OML 1 (custodial/closed) or unknown: barely a road

log = logging.getLogger("prepare_data")


def polygon_bounds(polygon_path: Path) -> tuple[float, float, float, float]:
    raw = json.loads(polygon_path.read_text())
    if raw.get("type") == "FeatureCollection":
        geoms = [f["geometry"] for f in raw["features"]]
    elif raw.get("type") == "Feature":
        geoms = [raw["geometry"]]
    else:
        geoms = [raw]
    projected = [rasterio.warp.transform_geom("EPSG:4326", TARGET_CRS, g)
                 for g in geoms]
    geom = unary_union([shape(g) for g in projected])
    minx, miny, maxx, maxy = geom.bounds
    return (minx - MARGIN_M, miny - MARGIN_M, maxx + MARGIN_M, maxy + MARGIN_M)


def make_grid(bounds) -> tuple[Affine, tuple[int, int]]:
    minx, miny, maxx, maxy = bounds
    cols = int(np.ceil((maxx - minx) / CELL_M))
    rows = int(np.ceil((maxy - miny) / CELL_M))
    return Affine(CELL_M, 0, minx, 0, -CELL_M, maxy), (rows, cols)


def warp_dem(dem_paths: list[Path], transform, shp, out: Path) -> None:
    dst = np.full(shp, np.nan, dtype=np.float32)
    for p in dem_paths:
        with rasterio.open(p) as src:
            tile = np.full(shp, np.nan, dtype=np.float32)
            rasterio.warp.reproject(
                source=rasterio.band(src, 1), destination=tile,
                dst_transform=transform, dst_crs=TARGET_CRS,
                dst_nodata=np.nan, resampling=Resampling.bilinear,
            )
            dst = np.where(np.isnan(dst), tile, dst)
    valid = np.isfinite(dst)
    log.info("DEM: %.1f%% of grid has data, %0.f-%0.f m",
             100 * valid.mean(), np.nanmin(dst), np.nanmax(dst))
    _write(out, np.where(valid, dst, -9999.0), "float32", transform,
           nodata=-9999.0)


def _write(path: Path, arr, dtype, transform, nodata=0) -> None:
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
        count=1, dtype=dtype, crs=TARGET_CRS, transform=transform,
        nodata=nodata, compress="deflate", tiled=True,
    ) as dst:
        dst.write(arr.astype(dtype), 1)
    log.info("wrote %s", path)


def write_hillshade(dem_path: Path, out: Path, azimuth_deg: float = 315.0,
                    altitude_deg: float = 45.0) -> None:
    """Standard Horn hillshade from the prepared DEM — a topo-style relief
    underlay for QGIS that matches the analysis grid exactly."""
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float64)
        dem[dem == src.nodata] = np.nan
        transform = src.transform
    cell = abs(transform.a)
    dz_dy, dz_dx = np.gradient(dem, cell)
    slope = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(-dz_dx, dz_dy)
    az = np.deg2rad(azimuth_deg)
    alt = np.deg2rad(altitude_deg)
    shaded = (np.sin(alt) * np.cos(slope)
              + np.cos(alt) * np.sin(slope) * np.cos(az - aspect))
    out_arr = np.where(np.isnan(shaded), 0,
                       np.clip(255 * shaded, 1, 255)).astype(np.uint8)
    _write(out, out_arr, "uint8", transform)


def rasterize(gdfs: list[gpd.GeoDataFrame], transform, shp) -> np.ndarray:
    shapes = []
    for gdf in gdfs:
        gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty]
        if len(gdf):
            shapes.extend((geom, 1) for geom in gdf.to_crs(TARGET_CRS).geometry)
    if not shapes:
        return np.zeros(shp, dtype=np.uint8)
    return rasterio.features.rasterize(
        shapes, out_shape=shp, transform=transform, fill=0, dtype="uint8",
        all_touched=True,
    )


def read_clipped(path: Path, bbox_target, layer=None, **kw) -> gpd.GeoDataFrame:
    """Read a vector source clipped to the target-CRS bbox (reprojected to
    the source CRS for the spatial filter)."""
    info_crs = pyogrio.read_info(path, layer=layer).get("crs") or "EPSG:4326"
    b = gpd.GeoSeries([box(*bbox_target)], crs=TARGET_CRS).to_crs(info_crs)
    gdf = gpd.read_file(path, layer=layer, bbox=tuple(b.total_bounds), **kw)
    log.info("read %s%s: %d features", Path(path).name,
             f":{layer}" if layer else "", len(gdf))
    return gdf


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--polygon", required=True, type=Path)
    ap.add_argument("--dem", required=True, type=Path, nargs="+",
                    help="One or more 3DEP GeoTIFF tiles (mosaicked)")
    ap.add_argument("--osm-roads", type=Path,
                    help="Geofabrik gis_osm_roads_free_1.shp")
    ap.add_argument("--usfs-roads", type=Path,
                    help="GeoJSON from scripts/fetch_usfs_roads.py")
    ap.add_argument("--nhd-gdb", type=Path, nargs="+", default=[],
                    help="NHD FileGDB(s) (.gdb or .zip), one per HUC8 "
                         "touching the polygon; layers are merged")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--crs", default=None,
                    help="Target CRS override (e.g. EPSG:26911 for UTM 11N "
                         "outside Montana); must match config target_crs "
                         "used at run time")
    args = ap.parse_args(argv)
    if args.crs:
        global TARGET_CRS
        TARGET_CRS = args.crs
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if not (args.osm_roads or args.usfs_roads):
        ap.error("need at least one of --osm-roads / --usfs-roads")

    args.out.mkdir(parents=True, exist_ok=True)
    bounds = polygon_bounds(args.polygon)
    transform, shp = make_grid(bounds)
    log.info("grid: %d x %d @ %.0f m (%s)", shp[0], shp[1], CELL_M, TARGET_CRS)

    warp_dem(args.dem, transform, shp, args.out / "dem.tif")
    write_hillshade(args.out / "dem.tif", args.out / "hillshade.tif")

    # Roads: burn type classes ascending so the FASTEST class wins overlaps.
    by_class: dict[int, list] = {c: [] for c in ROAD_CLASSES}
    if args.osm_roads:
        osm = read_clipped(args.osm_roads, bounds)
        before = len(osm)
        osm = osm[osm["fclass"].isin(OSM_CLASS)]
        log.info("OSM drivable filter: %d -> %d", before, len(osm))
        for cls, sub in osm.groupby(osm["fclass"].map(OSM_CLASS)):
            by_class[int(cls)].append(sub)
    if args.usfs_roads:
        usfs = read_clipped(args.usfs_roads, bounds)
        classes = usfs.apply(usfs_class, axis=1)
        for cls, sub in usfs.groupby(classes):
            by_class[int(cls)].append(sub)
    roads = np.zeros(shp, dtype=np.uint8)
    labels = {5: "highway", 4: "paved", 3: "gravel", 2: "dirt", 1: "jeep"}
    for cls in ROAD_CLASSES:
        if by_class[cls]:
            layer = rasterize(by_class[cls], transform, shp)
            roads = np.maximum(roads, (layer > 0).astype(np.uint8) * cls)
    for cls in reversed(ROAD_CLASSES):
        log.info("road class %d (%s): %d cells", cls, labels[cls],
                 int((roads == cls).sum()))
    _write(args.out / "roads.tif", roads, "uint8", transform)

    # Hydro classes: 1 = stream (NHDFlowline; bikes can cross at a cost),
    # 2 = waterbody (lakes/river polygons; never crossable). 2 wins overlaps.
    stream_gdfs, body_gdfs = [], []
    for gdb in args.nhd_gdb:
        # USGS zips contain a single <name>.gdb directory named like the zip.
        src = (f"/vsizip/{gdb}/{gdb.stem}.gdb" if gdb.suffix == ".zip"
               else str(gdb))
        layers = {l[0] for l in pyogrio.list_layers(src)}
        if "NHDFlowline" in layers:
            stream_gdfs.append(read_clipped(src, bounds, layer="NHDFlowline",
                                            force_2d=True, columns=[]))
        for layer in ("NHDArea", "NHDWaterbody"):
            if layer in layers:
                body_gdfs.append(read_clipped(src, bounds, layer=layer,
                                              force_2d=True, columns=[]))
    hydro = rasterize(stream_gdfs, transform, shp).astype(np.uint8)
    bodies = rasterize(body_gdfs, transform, shp)
    hydro[bodies > 0] = 2
    log.info("hydro: %d stream cells, %d waterbody cells",
             int((hydro == 1).sum()), int((hydro == 2).sum()))
    _write(args.out / "hydro.tif", hydro, "uint8", transform)
    log.info("done -> %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
