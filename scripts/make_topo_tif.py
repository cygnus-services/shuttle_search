"""Render a georeferenced USGS Topo GeoTIFF for a prepared area's extent.

Fetches the USGS National Map "USGSTopo" basemap over the DEM's bounding
box and writes a drag-and-drop GeoTIFF (EPSG:3857) next to the other
prepared layers. Offline afterwards, no tile-service fiddling in QGIS.

    python scripts/make_topo_tif.py --like data/prepared/jamul/dem.tif \
        --out data/prepared/jamul/usgs_topo.tif [--width 4096] [--imagery]
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from rasterio.transform import from_bounds

SERVICES = {
    "topo": "USGSTopo",
    "imagery": "USGSImageryTopo",
}


def make_topo(like: Path, out: Path, width: int, service: str) -> None:
    with rasterio.open(like) as s:
        b = s.bounds
        crs = s.crs
    xs, ys = rasterio.warp.transform(
        crs, "EPSG:3857",
        [b.left, b.right, b.left, b.right],
        [b.bottom, b.bottom, b.top, b.top])
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    h = int(round(width * (maxy - miny) / (maxx - minx)))
    url = (f"https://basemap.nationalmap.gov/arcgis/rest/services/"
           f"{SERVICES[service]}/MapServer/export?" + urllib.parse.urlencode({
               "bbox": f"{minx},{miny},{maxx},{maxy}",
               "bboxSR": 3857, "imageSR": 3857,
               "size": f"{width},{h}", "format": "tiff", "f": "image"}))
    print(f"requesting {width}x{h} {service} tile...")
    raw = urllib.request.urlopen(url, timeout=180).read()
    with rasterio.open(io.BytesIO(raw)) as src:
        img = src.read()  # (bands, h, w), no georef
    rgb = img[:3]  # drop alpha if present
    transform = from_bounds(minx, miny, maxx, maxy, rgb.shape[2], rgb.shape[1])
    with rasterio.open(
        out, "w", driver="GTiff", height=rgb.shape[1], width=rgb.shape[2],
        count=3, dtype="uint8", crs="EPSG:3857", transform=transform,
        compress="jpeg", photometric="ycbcr",
    ) as dst:
        dst.write(rgb)
    print(f"wrote {out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--like", required=True, type=Path,
                    help="A prepared raster whose extent/CRS to match")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--width", type=int, default=4096)
    ap.add_argument("--imagery", action="store_true",
                    help="Use imagery+topo hybrid instead of the line topo")
    args = ap.parse_args(argv)
    make_topo(args.like, args.out, args.width,
              "imagery" if args.imagery else "topo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
