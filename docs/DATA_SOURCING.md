# Data Sourcing Guide — Montana Shuttle Trail Scout

A shopping list for every dataset the pipeline needs, with verified download
links (checked July 2026), formats, and the preprocessing needed to turn them
into the raster inputs `shuttle_scout` consumes.

**Test areas covered below:**

* **Anaconda** — Deer Lodge County; the Anaconda Range / Mount Haggin area
  south-west of town (big vertical, dense Forest Service road network).
* **Phillips** — interpreted as **Phillips County** (Malta area). The only
  meaningful vertical there is the **Little Rocky Mountains** on the county's
  southern edge. If you meant a different "Philips," only the tile/HUC IDs
  change — the sources are identical.

The pipeline needs four rasters on any grid/CRS (it reprojects internally):

| Input | Content | Source |
|---|---|---|
| `--dem` | elevation (meters) | USGS 3DEP 1/3 arc-second |
| `--roads` | nonzero = drivable road | OSM (Geofabrik) **merged with** USFS roads |
| `--hydro` | nonzero = river/stream/waterbody | USGS NHD (or MT State Library) |
| `--terrain` (optional) | land-cover class codes | NLCD (MRLC) |

Plus a `--polygon` GeoJSON search area (draw one at <https://geojson.io>, save
as WGS84 — that is what the GeoJSON spec mandates and what the loader assumes).

---

## 1. Elevation — USGS 3DEP 1/3 arc-second DEM (~10 m)

**Authoritative source:** USGS National Map.

* Point-and-click portal: <https://apps.nationalmap.gov/downloader/> —
  check *Elevation Products (3DEP) > 1/3 arc-second DEM > GeoTIFF*, draw your
  extent, hit *Search Products*.
* Faster: tiles sit on a public S3 bucket with a predictable URL pattern
  (verified working):

  ```
  https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/{TILE}/USGS_13_{TILE}.tif
  ```

  A tile named `n47w113` covers **46–47°N, 113–112°W** (the name is the
  NORTH-WEST corner). Each is a ~350–450 MB GeoTIFF, EPSG:4269 (NAD83
  geographic), elevations in **meters** (the pipeline default).

| Area | Tiles |
|---|---|
| Anaconda / Mount Haggin (46.0–46.3°N, 113.3–112.8°W) | `n47w113` (add `n47w114` if you push west past −113°) |
| Little Rocky Mtns, Phillips Co. (47.8–48.0°N, 108.8–108.4°W) | `n48w109` |

```bash
curl -O https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n47w113/USGS_13_n47w113.tif
curl -O https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n48w109/USGS_13_n48w109.tif
```

**Preprocessing:** none required — feed the GeoTIFF straight to `--dem`.
If your polygon spans two tiles, mosaic first:
`gdal_merge.py -o dem.tif USGS_13_n47w113.tif USGS_13_n47w114.tif`
(or `scripts/prepare_data.py` handles clipping to your polygon).

## 2. Roads — OpenStreetMap via Geofabrik

**Source (verified):** <https://download.geofabrik.de/north-america/us/montana.html>

* `montana-latest-free.shp.zip` (~181 MB) — shapefiles, the easy path.
* `montana-latest.osm.pbf` (~94 MB) or `montana-latest-free.gpkg.zip`
  (~186 MB) if you prefer PBF/GeoPackage.

```bash
curl -O https://download.geofabrik.de/north-america/us/montana-latest-free.shp.zip
unzip montana-latest-free.shp.zip -d montana_osm
```

> Tip: `-latest` files are re-published daily; a download that stalls and
> resumes across the republish window ends up silently corrupt (right size,
> broken zip — verify with `unzip -t`). The dated snapshots
> (`montana-YYMMDD-free.shp.zip`, e.g. `montana-260701-free.shp.zip`) are
> immutable and safe to resume.

The layer you want is `gis_osm_roads_free_1.shp` (EPSG:4326). Keep the
drivable classes and drop footpaths: `fclass` in
`motorway|trunk|primary|secondary|tertiary|unclassified|residential|service|track`
(and their `*_link` variants). `track` is critical in Montana — that's most
logging/ranch roads. `scripts/prepare_data.py` applies exactly this filter.

## 3. Forest Service roads (incl. old/low-standard logging roads)

OSM misses many USFS system roads, and nearly all decommissioned ones. Three
complementary sources, all from the USFS Enterprise Data Warehouse (EDW):

1. **REST query (verified; what `scripts/fetch_usfs_roads.py` automates)** —
   pull just your bounding box as GeoJSON, no giant national download:

   ```
   https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_RoadBasic_01/MapServer/0/query
     ?where=1=1&geometry=<minlon>,<minlat>,<maxlon>,<maxlat>
     &geometryType=esriGeometryEnvelope&inSR=4326
     &spatialRel=esriSpatialRelIntersects&outFields=*&outSR=4326&f=geojson
   ```

   Layer "National Forest System Roads", max 2000 records/page,
   pagination supported (`resultOffset`).

2. **Bulk downloads** — <https://data.fs.usda.gov/geodata/edw/datasets.php>
   (browse) or the discovery portal <https://data-usfs.hub.arcgis.com/>
   (search, filter by extent, export shapefile/GeoJSON). Datasets to grab:
   * **RoadCore (FS)** — every *system* road with `OPER_MAINT_LEVEL`
     (1 = closed/custodial, 2 = high-clearance, 3–5 = passenger car). Keep
     levels 2–5 for the *shuttle* network; levels 1–2 are still gold for
     finding old road benches to ride.
   * **Motor Vehicle Use Map (MVUM) roads** — what you may legally drive,
     with seasonal closures.
   * **Road Decommissioned** — removed from the system; often still visible
     benches: candidate descent corridors, NOT drivable access. Keep them in
     a separate file if you want them scored differently later.
3. **County/local roads** the USFS layers lack are already in OSM, which is
   why the prep script **merges OSM + USFS** into one road raster (simple
   union — a cell is "road" if either source says so).

The relevant National Forests: **Beaverhead-Deerlodge** (Anaconda area);
the Little Rockies are mostly **BLM land** (fewer USFS roads — the
REST query simply returns less there; OSM + BLM GTLF roads carry more weight).

## 4. Hydrography — USGS NHD High Resolution

**Sources:**

* National Map downloader (check *Hydrography > NHD > FileGDB*), or direct
  S3 by 8-digit hydrologic unit (verified working):

  ```
  https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHD/HU8/GDB/NHD_H_{HUC8}_HU8_GDB.zip
  ```

* Montana State Library MSDI Hydrography (statewide, same NHD lineage):
  <https://msl.mt.gov/geoinfo/msdi/hydrography>

| Area | HUC8 |
|---|---|
| Anaconda / Mount Haggin | `17010201` (Upper Clark Fork) |
| Little Rocky Mtns | Milk River basin — look up the exact HUC8 for your polygon on the downloader map (candidates: Peoples Creek `10050011`, Beaver Creek side) |

```bash
curl -O https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHD/HU8/GDB/NHD_H_17010201_HU8_GDB.zip
```

Inside the FileGDB, the layers that matter: `NHDFlowline` (streams/rivers —
filter to `FCode` 46006 perennial if intermittent gullies over-constrain
routes), `NHDArea` (river polygons), `NHDWaterbody` (lakes). GDAL/pyogrio
reads FileGDB natively; no conversion needed before the prep script.
Note: NHD is in maintenance/archive mode (superseded by 3DHP) but the staged
downloads remain the authoritative high-res hydro for Montana.

## 5. Optional terrain classification — NLCD land cover

<https://www.mrlc.gov/viewer/> — export *Annual NLCD Land Cover* for your
extent as GeoTIFF (30 m). The default config excludes classes 11 (open
water), 12 (ice/snow), 23/24 (developed). Purely additive — the pipeline
runs fine without it.

---

## 6. Preprocessing — vectors to the raster inputs

The pipeline wants *rasters*. Two provided scripts do everything:

```bash
# 1) Fetch USFS roads for a bbox (REST query above):
.venv/bin/python scripts/fetch_usfs_roads.py --bbox -113.30 45.95 -112.80 46.30 \
    --out data/raw/usfs_roads_anaconda.geojson

# 2) Clip + reproject + rasterize everything onto a DEM-aligned grid:
.venv/bin/python scripts/prepare_data.py \
    --polygon examples/anaconda_search_area.geojson \
    --dem data/raw/USGS_13_n47w113.tif \
    --osm-roads data/raw/montana_osm/gis_osm_roads_free_1.shp \
    --usfs-roads data/raw/usfs_roads_anaconda.geojson \
    --nhd-gdb data/raw/NHD_H_17010201_HU8_GDB.zip \
    --out data/prepared/anaconda
```

`prepare_data.py` reprojects everything to **EPSG:26912 (NAD83 / UTM 12N,
meters)** at 10 m, writes `dem.tif`, `roads.tif` (OSM ∪ USFS, burned 1),
and `hydro.tif` (flowlines + waterbodies, burned 1), each clipped to the
polygon bounds plus margin. Then:

```bash
.venv/bin/python -m shuttle_scout \
    --polygon examples/anaconda_search_area.geojson \
    --dem data/prepared/anaconda/dem.tif \
    --roads data/prepared/anaconda/roads.tif \
    --hydro data/prepared/anaconda/hydro.tif \
    --out out/anaconda
```

### How differing GSDs are reconciled

Sources arrive at very different ground sample distances — 3DEP DEM at
1/3 arc-second (~10.3 m N–S but only ~7.1 m E–W at 46.5°N, because it's a
geographic grid), NLCD at 30 m, and vectors (roads, hydro) with no GSD at
all. The strategy:

* **One analysis grid, chosen to match the finest source.** Everything is
  warped once onto a square 10 m grid in EPSG:26912 (meters), so the DEM
  loses essentially nothing and all later math (slope, distances, routing)
  is GSD-agnostic. Nothing is ever compared across grids.
* **Resampling follows data type.** Continuous fields (elevation) use
  bilinear; categorical/binary fields (roads, hydro, NLCD classes) use
  nearest — bilinear on categories would invent nonexistent classes, and
  nearest on elevation would create phantom slope steps.
* **Coarser rasters are oversampled, not sharpened.** NLCD 30 m cells just
  repeat across ~3×3 analysis cells; blocky but honest.
* **Vectors are burned with `all_touched=True`** so a road or stream marks
  every 10 m cell it touches — narrow linear features can't fall through
  the grid, at the cost of being ~1 cell generous (conservative in the
  right direction for hazards; slightly optimistic for road access, which
  the 1/4-mile access threshold dwarfs anyway).
* **Slope is computed after projection**, on the metric grid, so
  degree-vs-meter anisotropy in the source DEM never touches grade math.
* `cell_size_m` is a config knob: run 30 m for a fast statewide sweep,
  then 10 m on shortlisted polygons.

### CRS and unit conventions (assumptions, stated once)

* Target CRS **EPSG:26912** — meters, low distortion across Montana.
* 3DEP elevations are **meters** (pass `--dem-units feet` if you ever feed a
  feet-based DEM); user-facing thresholds are **feet/miles/minutes**.
* GeoJSON inputs are **WGS84** per RFC 7946.
* Rasterized roads are optimistic (a mapped road may be gated or washed
  out) — that's what the QGIS ground-truthing pass is for.
