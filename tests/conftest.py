"""Shared fixtures: a small synthetic mountain scene on EPSG:26912.

Scene layout (2 km x 2 km at 20 m cells, plus polygon padding):

* DEM: plane dropping west-to-east at ~12% grade (high on the WEST side),
  total relief ~240 m (~790 ft) per 2 km... scaled to guarantee > 1000 ft
  over the full extent by using a 4 km wide scene.
* Roads: an "L": north-south road near the west (high) edge, east-west road
  along the south edge reaching the east (low) side.
* Hydro: a short stream segment in the far northeast, away from the road L.

This gives at least one start (high, near west road) and one end (low, near
south-east road) that satisfies every hard constraint by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import rasterio.warp
from rasterio.transform import from_origin

CRS = "EPSG:26912"
CELL = 20.0
NX, NY = 220, 120          # cols, rows -> 4.4 km x 2.4 km
X0, Y0 = 300000.0, 5200000.0  # top-left corner (UTM meters)


def _write_tif(path: Path, arr: np.ndarray, dtype: str) -> None:
    transform = from_origin(X0, Y0, CELL, CELL)
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
        count=1, dtype=dtype, crs=CRS, transform=transform,
    ) as dst:
        dst.write(arr.astype(dtype), 1)


@pytest.fixture(scope="session")
def synthetic_scene(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("scene")

    cols = np.arange(NX)[None, :].repeat(NY, axis=0)
    # 12% westward-facing slope: high (x=0/west) -> low (x=east).
    # Relief = 0.12 * 20 m * 220 = 528 m (~1730 ft).
    dem = 2400.0 - 0.12 * CELL * cols.astype(np.float64)
    _write_tif(d / "dem.tif", dem, "float32")

    # Roads must sit INSIDE the search polygon (8-cell margin below).
    # Value 3 = "good" quality class (see scripts/prepare_data.py).
    roads = np.zeros((NY, NX), dtype=np.uint8)
    roads[:, 12] = 3         # N-S road near the west (high) edge
    roads[NY - 13, :] = 3    # E-W road along the south edge
    _write_tif(d / "roads.tif", roads, "uint8")

    hydro = np.zeros((NY, NX), dtype=np.uint8)
    hydro[5:15, NX - 15] = 1  # short stream, far northeast
    _write_tif(d / "hydro.tif", hydro, "uint8")

    # Search polygon: interior of the scene (leave a raster margin), WGS84.
    margin = 8 * CELL
    utm_box = {
        "type": "Polygon",
        "coordinates": [[
            [X0 + margin, Y0 - margin],
            [X0 + NX * CELL - margin, Y0 - margin],
            [X0 + NX * CELL - margin, Y0 - NY * CELL + margin],
            [X0 + margin, Y0 - NY * CELL + margin],
            [X0 + margin, Y0 - margin],
        ]],
    }
    wgs_box = rasterio.warp.transform_geom(CRS, "EPSG:4326", utm_box)
    poly_path = d / "polygon.geojson"
    poly_path.write_text(json.dumps(
        {"type": "Feature", "geometry": wgs_box, "properties": {}}))

    return {
        "dir": d,
        "polygon": poly_path,
        "dem": d / "dem.tif",
        "roads": d / "roads.tif",
        "hydro": d / "hydro.tif",
    }


@pytest.fixture
def fast_config():
    from shuttle_scout.config import Config

    cfg = Config()
    cfg.params.cell_size_m = 20.0
    cfg.params.local_extrema_window_m = 300.0
    cfg.params.max_start_candidates = 15
    cfg.params.max_end_candidates = 15
    cfg.params.max_ranked_pairs = 10
    cfg.validate()
    return cfg
