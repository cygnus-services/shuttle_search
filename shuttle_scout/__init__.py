"""Shuttle Scout — automated shuttle mountain-bike trail scouting for Montana.

Pipeline stages (each is an importable module):

    data_loading  -> load & validate rasters + search polygon, unify grid/CRS
    terrain       -> slope, hazard masks, distance-to-road surfaces
    candidates    -> candidate start/end points and hard-constraint pair filtering
    routing       -> least-cost descent routes and shuttle drive-time estimates
    scoring       -> weighted soft scoring / ranking of surviving pairs
    output        -> multi-band GeoTIFF + ranked CSV/GeoJSON for QGIS
    pipeline      -> orchestrates the above end to end

All internal computation is done in METERS on a single common raster grid
(the DEM's grid, reprojected to a projected CRS). User-facing configuration
is expressed in feet / miles / minutes and converted exactly once at the
config boundary (see config.py and units.py).
"""

__version__ = "0.1.0"
