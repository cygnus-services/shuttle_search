import numpy as np

from shuttle_scout.terrain import road_grade_estimate


def _plane(nrows=60, ncols=60, slope_per_cell=0.0):
    cols = np.arange(ncols)[None, :].repeat(nrows, axis=0)
    return 2000.0 - slope_per_cell * cols.astype(np.float64)


def test_flat_road_grade_zero():
    dem = _plane()
    roads = np.zeros(dem.shape, dtype=bool)
    roads[30, :] = True
    grade = road_grade_estimate(dem, roads, window_m=250.0, cell_size_m=10.0)
    assert np.nanmax(grade[roads]) == 0.0


def test_contour_road_on_steep_slope_is_gentle():
    # 30% cross-slope, but the road follows a contour (constant elevation).
    dem = _plane(slope_per_cell=3.0)  # 3 m per 10 m cell = 30%
    roads = np.zeros(dem.shape, dtype=bool)
    roads[:, 30] = True  # N-S road, constant elevation column
    grade = road_grade_estimate(dem, roads, window_m=250.0, cell_size_m=10.0)
    assert np.nanmax(grade[roads]) == 0.0


def test_fall_line_road_grade_measured():
    # Road straight down a 30% slope: windowed estimate ~= 30%.
    dem = _plane(slope_per_cell=3.0)
    roads = np.zeros(dem.shape, dtype=bool)
    roads[30, :] = True  # E-W road, down the gradient
    grade = road_grade_estimate(dem, roads, window_m=250.0, cell_size_m=10.0)
    mid = grade[30, 30]
    assert 25.0 <= mid <= 32.0


def test_offroad_cells_are_nan():
    dem = _plane()
    roads = np.zeros(dem.shape, dtype=bool)
    roads[30, :] = True
    grade = road_grade_estimate(dem, roads, window_m=250.0, cell_size_m=10.0)
    assert np.isnan(grade[10, 10])
