import numpy as np
import pandas as pd

from orbitcu import hotspot


def _s2(n=50, hot=0, cloud=0.0, baseline="05.11"):
    off = 1000 if float(baseline) >= 4 else 0
    b8a = np.full((n, n), 2500.0) + off
    b11 = np.full((n, n), 2000.0) + off
    b12 = np.full((n, n), 1500.0) + off
    scl = np.full((n, n), 4)
    if hot:
        b12[:1, :hot] = 15000 + off
        b11[:1, :hot] = 6000 + off
        b8a[:1, :hot] = 1500 + off
    k = int(cloud * n * n)
    scl.ravel()[n * n - k:] = 9
    return np.stack([b8a, b11, b12, scl])


def test_hot_and_quality_flags():
    r = hotspot.score_image("sentinel2", _s2(hot=5), "05.11")
    assert r["quality"] == "clear" and r["n_hot"] == 5 and r["hot_seen"] and not r["cold_seen"]
    r = hotspot.score_image("sentinel2", _s2(), "05.11")
    assert r["n_hot"] == 0 and r["cold_seen"]
    # a zero under clouds is not "cold"; a hot pixel under partial cloud is still hot
    r = hotspot.score_image("sentinel2", _s2(cloud=0.7), "05.11")
    assert r["quality"] == "cloudy" and not r["cold_seen"]
    r = hotspot.score_image("sentinel2", _s2(hot=3, cloud=0.3), "05.11")
    assert r["quality"] == "partial" and r["hot_seen"]


def test_s2_offset_and_weak_candidates():
    # old baseline (no +1000 offset) gives the same result
    assert hotspot.score_image("sentinel2", _s2(hot=4, baseline="03.01"), "03.01")["n_hot"] == 4
    # a mild B12 > B11 surface (metal roof) is a candidate, not a confirmed hot pixel
    a = _s2()
    a[2, 10, 10] = 1000 + 5000; a[1, 10, 10] = 1000 + 3500; a[0, 10, 10] = 1000 + 2500
    r = hotspot.score_image("sentinel2", a, "05.11")
    assert r["n_candidate"] == 1 and r["n_hot"] == 0


def test_dedupe_keeps_best_copy():
    t = pd.Timestamp("2025-06-19 08:22:20", tz="UTC")
    s = pd.DataFrame({"site_id": "X", "sensor": "landsat", "platform": "landsat-9",
                      "acquired_at": [t, t + pd.Timedelta(seconds=24), t + pd.Timedelta(days=8)],
                      "quality": ["partial", "clear", "clear"], "nodata_frac": [0.2, 0.0, 0.0]})
    d = hotspot.dedupe_scores(s)
    assert len(d) == 2 and d.iloc[0]["quality"] == "clear"
