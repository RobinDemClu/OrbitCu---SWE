import math

import pytest

from orbitcu.sites import Site, utm_crs


def test_utm_zone_north_south():
    assert utm_crs(53.5, 10.0).to_epsg() == 32632
    assert utm_crs(-10.8, 25.2).to_epsg() == 32735
    assert utm_crs(69.3, 88.1).to_epsg() == 32645


@pytest.mark.parametrize("lat,lon", [(53.533, 10.035), (-10.78, 25.25), (69.33, 88.12)])
def test_thermal_box_is_metric(lat, lon):
    s = Site("X", "x", "x", lat, lon, thermal_half_km=1.5)
    w, so, e, n = s.thermal_bbox()
    # 3 km tall box ~ 0.027 deg latitude everywhere
    assert n - so == pytest.approx(3.0 / 111.0, rel=0.02)
    # width in degrees grows with 1/cos(lat)
    assert e - w == pytest.approx(3.0 / (111.32 * math.cos(math.radians(lat))), rel=0.03)
    assert w < lon < e and so < lat < n


def test_so2_disc_radius():
    s = Site("X", "x", "x", 51.69, 15.98, so2_radius_km=50)
    w, so, e, n = s.so2_bbox()
    assert (n - so) * 111.0 == pytest.approx(100, rel=0.02)
