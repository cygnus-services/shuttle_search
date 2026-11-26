"""End-to-end pipeline test on the synthetic scene (see conftest.py)."""

import numpy as np
import rasterio

from shuttle_scout import pipeline
from shuttle_scout.output import BAND_NAMES


def test_end_to_end(synthetic_scene, fast_config, tmp_path):
    out = tmp_path / "out"
    ranked = pipeline.run(
        polygon_path=synthetic_scene["polygon"],
        dem_path=synthetic_scene["dem"],
        roads_path=synthetic_scene["roads"],
        hydro_path=synthetic_scene["hydro"],
        out_dir=out,
        cfg=fast_config,
    )

    assert ranked, "synthetic scene should yield at least one viable pair"
    best = ranked[0]
    # Hard constraints hold on every ranked pair.
    for p in ranked:
        assert p.drop_ft >= fast_config.hard.min_drop_ft - 0.1
        assert (fast_config.hard.min_avg_grade_pct
                <= p.avg_grade_pct
                <= fast_config.hard.max_avg_grade_pct)
        assert p.drive_time_s <= fast_config.hard.max_drive_time_s
        assert 1 <= len(p.routes) <= 2
        for r in p.routes:
            assert not r.crosses_hazard
    # Start must be the high (west) side.
    assert best.start.elev_m > best.end.elev_m

    # Multi-band GeoTIFF exists with the documented bands.
    tif = out / "shuttle_candidates.tif"
    assert tif.exists()
    with rasterio.open(tif) as src:
        assert src.count == len(BAND_NAMES)
        assert tuple(src.descriptions) == BAND_NAMES
        starts = src.read(1)
        routes = src.read(3)
        roads = src.read(4)
        assert (starts > 0).any()
        assert (routes > 0).any()
        assert (roads > 0).any()
        # Rank values are small positive ints, 0 = nodata.
        assert starts.max() <= fast_config.params.max_ranked_pairs

    assert (out / "ranked_pairs.csv").read_text().count("\n") >= 2
    # Separate QGIS-friendly layers, each with a .qml style sidecar.
    for name in ("start_points.geojson", "end_points.geojson",
                 "routes.geojson", "roads_overlay.tif",
                 "hazards_overlay.tif"):
        assert (out / name).exists(), name
        qml = (out / name).with_suffix(".qml")
        assert qml.exists() and qml.read_text().startswith("<!DOCTYPE qgis")
    legend = (out / "color_legend.svg").read_text()
    assert legend.startswith("<svg") and "#1" in legend
    md = (out / "ranked_pairs.md").read_text()
    assert "| #" in md and "drop (ft)" in md
