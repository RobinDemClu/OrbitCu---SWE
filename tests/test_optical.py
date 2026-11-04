import numpy as np

from orbitcu.ingest import optical
from orbitcu.sites import Site


def test_s2_quality_fractions():
    qa = np.array([[4, 4, 8, 9], [3, 0, 4, 10]])  # veg, veg, cloud, cloud / shadow, nodata, veg, cirrus
    q = optical.quality_fractions(optical.SENTINEL2, qa)
    assert q["aoi_nodata_frac"] == 0.125
    assert q["aoi_cloud_frac"] == round(3 / 7, 4)
    assert q["aoi_shadow_frac"] == round(1 / 7, 4)


def test_landsat_quality_bits():
    clear = 21824          # typical clear-land QA_PIXEL value
    cloud = 1 << 3 | 1 << 1 | 1 << 6
    shadow = 1 << 4 | 1 << 6
    q = optical.quality_fractions(optical.LANDSAT, np.array([[clear, cloud], [shadow, 1]]))
    assert q["aoi_nodata_frac"] == 0.25
    assert q["aoi_cloud_frac"] == round(1 / 3, 4)
    assert q["aoi_shadow_frac"] == round(1 / 3, 4)


def test_aoi_window_snaps_to_grid():
    s = Site("X", "x", "x", 53.533, 10.035, thermal_half_km=1.5)
    minx, miny, maxx, maxy = optical.aoi_window(s, "EPSG:32632", 20)
    assert all(v % 20 == 0 for v in (minx, miny, maxx, maxy))
    assert 3000 <= maxx - minx <= 3040
