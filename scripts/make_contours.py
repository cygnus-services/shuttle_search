"""Generate topo contour lines from a prepared DEM.

Writes a GeoPackage of contour lines (attribute ``elev_ft``, plus
``index`` = 1 on every Nth line for heavier styling) and a .qml sidecar
so QGIS renders thin gray intermediates and labeled amber index contours
on load. Offline, zoom-independent, and derived from the exact DEM the
analysis uses.

Usage:
    python scripts/make_contours.py --dem data/prepared/anaconda/dem.tif \
        --out data/prepared/anaconda/contours.gpkg \
        [--interval-ft 100] [--index-every 5]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import contourpy
import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import LineString

FEET_PER_METER = 3.280839895

log = logging.getLogger("make_contours")

QML = """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology|Labeling" version="3.28.0" labelsEnabled="1">
  <renderer-v2 type="RuleRenderer" forceraster="0" enableorderby="0" symbollevels="0">
    <rules key="{rules}">
      <rule key="r0" symbol="0" filter="&quot;index&quot; = 0" label="contour"/>
      <rule key="r1" symbol="1" filter="&quot;index&quot; = 1" label="index contour"/>
    </rules>
    <symbols>
      <symbol type="line" name="0" alpha="0.55" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <prop k="line_color" v="120,90,60,255"/>
          <prop k="line_style" v="solid"/>
          <prop k="line_width" v="0.20"/>
          <prop k="line_width_unit" v="MM"/>
        </layer>
      </symbol>
      <symbol type="line" name="1" alpha="0.8" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <prop k="line_color" v="196,132,50,255"/>
          <prop k="line_style" v="solid"/>
          <prop k="line_width" v="0.45"/>
          <prop k="line_width_unit" v="MM"/>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="rule-based">
    <rules key="{lrules}">
      <rule key="lr1" filterExpression="&quot;index&quot; = 1" description="index labels">
        <settings calloutType="simple">
          <text-style fieldName="format_number(&quot;elev_ft&quot;, 0) || ' ft'"
                      isExpression="1" fontSize="8"
                      textColor="140,90,30,255" fontSizeUnit="Point">
            <text-buffer bufferDraw="1" bufferSize="0.8" bufferSizeUnits="MM"
                         bufferColor="255,255,255,220"/>
          </text-style>
          <placement placement="2"/>
        </settings>
      </rule>
    </rules>
  </labeling>
</qgis>
"""


def make_contours(dem_path: Path, out: Path, interval_ft: float,
                  index_every: int, simplify_m: float) -> None:
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float64)
        dem[dem == src.nodata] = np.nan
        transform = src.transform
        crs = src.crs

    elev_ft = dem * FEET_PER_METER
    lo = np.floor(np.nanmin(elev_ft) / interval_ft) * interval_ft
    hi = np.ceil(np.nanmax(elev_ft) / interval_ft) * interval_ft
    levels = np.arange(lo, hi + interval_ft, interval_ft)
    log.info("contouring %s: %.0f-%.0f ft, %d levels @ %.0f ft",
             dem_path.name, lo, hi, len(levels), interval_ft)

    gen = contourpy.contour_generator(z=elev_ft, name="serial",
                                      corner_mask=True)
    records = []
    for level in levels:
        idx = 1 if (round(level / interval_ft) % index_every == 0) else 0
        for verts in gen.lines(level):
            if len(verts) < 2:
                continue
            # contourpy returns (col, row) pixel coords -> map coords.
            xs, ys = rasterio.transform.xy(
                transform, verts[:, 1], verts[:, 0], offset="center")
            line = LineString(zip(xs, ys))
            if simplify_m > 0:
                line = line.simplify(simplify_m)
            if line.length > 0:
                records.append(
                    {"elev_ft": float(level), "index": idx, "geometry": line})

    gdf = gpd.GeoDataFrame(records, crs=crs)
    gdf.to_file(out, driver="GPKG")
    out.with_suffix(".qml").write_text(QML.format(rules="rules", lrules="l"))
    log.info("wrote %s (%d lines) + style sidecar", out, len(gdf))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dem", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--interval-ft", type=float, default=100.0)
    ap.add_argument("--index-every", type=int, default=5,
                    help="every Nth contour is a heavy labeled index line")
    ap.add_argument("--simplify-m", type=float, default=5.0,
                    help="Douglas-Peucker tolerance (0 = keep all vertices)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    make_contours(args.dem, args.out, args.interval_ft, args.index_every,
                  args.simplify_m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
