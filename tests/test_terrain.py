import numpy as np
import pytest

from shuttle_scout.terrain import (
    buffer_mask,
    compute_slope_percent,
    distance_to_mask_m,
)


def test_slope_on_tilted_plane():
    # 10% slope along x at 10 m cells: z = 1000 - 1.0 per cell.
    cols = np.arange(50)[None, :].repeat(40, axis=0)
    dem = 1000.0 - 1.0 * cols
    slope = compute_slope_percent(dem, cell_size_m=10.0)
    assert slope[20, 25] == pytest.approx(10.0, abs=0.01)


def test_slope_flat_is_zero():
    dem = np.full((20, 20), 1500.0)
    slope = compute_slope_percent(dem, cell_size_m=10.0)
    assert float(slope.max()) == pytest.approx(0.0)


def test_slope_nan_propagates():
    dem = np.full((10, 10), 1000.0)
    dem[5, 5] = np.nan
    slope = compute_slope_percent(dem, cell_size_m=10.0)
    assert np.isnan(slope[5, 5])


def test_buffer_mask_grows_by_distance():
    mask = np.zeros((21, 21), dtype=bool)
    mask[10, 10] = True
    grown = buffer_mask(mask, buffer_m=25.0, cell_size_m=10.0)
    assert grown[10, 12]       # 20 m away: inside
    assert not grown[10, 14]   # 40 m away: outside


def test_distance_to_mask():
    mask = np.zeros((10, 10), dtype=bool)
    mask[0, :] = True
    dist = distance_to_mask_m(mask, cell_size_m=10.0)
    assert dist[0, 5] == 0.0
    assert dist[3, 5] == pytest.approx(30.0)


def test_distance_to_empty_mask_is_inf():
    dist = distance_to_mask_m(np.zeros((5, 5), dtype=bool), cell_size_m=10.0)
    assert np.isinf(dist).all()
