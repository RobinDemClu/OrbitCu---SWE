import numpy as np
import pandas as pd

from orbitcu import geolocate as g


def test_clusters_finds_dense_source():
    rng = np.random.default_rng(0)
    n = 200
    dense = pd.DataFrame({"latitude": 51.688 + rng.normal(0, 0.001, n), "longitude": 15.980 + rng.normal(0, 0.0015, n),
                          "acq_date": [f"2026-01-{1 + i % 28:02d}" for i in range(n)], "type": 2})
    noise = pd.DataFrame({"latitude": 51.6 + rng.uniform(0, 0.2, 30), "longitude": 15.9 + rng.uniform(0, 0.2, 30),
                          "acq_date": "2026-02-01", "type": 0})
    c = g.clusters(pd.concat([dense, noise]), 51.68, 15.99)
    assert abs(c.iloc[0]["lat"] - 51.688) < 0.001 and abs(c.iloc[0]["lon"] - 15.980) < 0.001
    assert c.iloc[0]["share_static"] > 0.9 and c.iloc[0]["n_days"] == 28


def test_monthly_detection_days_and_verdict():
    from orbitcu.activity_check import monthly_detection_days, verdict
    df = pd.DataFrame({"latitude": [10.0] * 5 + [10.5], "longitude": [20.0] * 6,
                       "acq_date": ["2026-01-02", "2026-01-02", "2026-01-05", "2026-03-01", "2026-03-04", "2026-03-04"]})
    m = monthly_detection_days(df, 10.0, 20.0, start="2026-01", end="2026-03")
    assert list(m.values) == [2, 0, 2]
    assert verdict(m, last_n=1) == "active" and verdict(m, last_n=2) == "intermittent"


def test_statusmap_build_data(tmp_path):
    from orbitcu.statusmap import build_data, render
    rows = pd.DataFrame({"latitude": [51.68775] * 3, "longitude": [15.98032] * 3,
                         "acq_date": ["2026-01-02", "2026-01-03", "2026-02-10"]})
    rows.to_parquet(tmp_path / "PL-GLOGOW.parquet")
    data = build_data(tmp_path, "2026-01", "2026-03")
    glogow = next(s for s in data["sites"] if s["id"] == "PL-GLOGOW")
    assert data["months"] == ["2026-01", "2026-02", "2026-03"]
    assert glogow["days"] == [2, 1, 0] and glogow["baseline"] == 0.0  # < 3 months with heat
    html = render(data, tmp_path / "map.html").read_text()
    assert "/*__DATA__*/" not in html and "PL-GLOGOW" in html


def test_typical_level_ignores_outage_months():
    from orbitcu.statusmap import typical_level
    assert typical_level([0, 0, 0, 20, 22, 24]) == 22.0
    assert typical_level([0, 5, 0, 6]) == 0.0
