import numpy as np
import pandas as pd

from orbitcu import production as P


class _Site:
    def __init__(self, id, lat=0.0, lon=0.0, cap=100.0):
        self.id, self.lat, self.lon, self.capacity_kt = id, lat, lon, cap


def _tcc(start="2020-01-01", end="2021-12-31", cloudy_days=()):
    t = pd.date_range(start, pd.Timestamp(end) + pd.Timedelta(hours=23), freq="h", tz="UTC")
    tcc = np.where(t.normalize().tz_convert(None).isin(pd.DatetimeIndex(cloudy_days)), 1.0, 0.0)
    return pd.DataFrame({"valid_time": t, "tcc": tcc})


def test_clear_days_uses_overpass_hours():
    c = P.clear_days(_tcc(cloudy_days=["2020-01-05"]), lon=0.0)
    assert not c[pd.Timestamp("2020-01-05")]
    assert c[pd.Timestamp("2020-01-06")]


def test_shutdown_factor():
    assert list(P.shutdown_factor([0.3, 0.59, 0.6, 1.4])) == [0.3, 0.59, 1.0, 1.0]


def test_activity_from_masks_and_hot_frac(tmp_path):
    _tcc().assign(site_id="A").to_parquet(tmp_path / "A_x.parquet")
    masks = pd.DataFrame({"site_id": ["A", "A"], "month": ["2020-01", "2020-01"],
                          "mask": ["00000003", "00000006"], "n_det": [2, 2], "frp_sum": [10, 5]})
    a = P.activity_from_masks(masks, [_Site("A")], tmp_path, "2020-01", "2020-02", nodata={"2020-02"})
    jan = a[a.month == pd.Period("2020-01", "M")].iloc[0]
    assert jan.hot_days == 3 and jan.n_det == 4 and jan.frp_sum == 15  # days 1,2,3 (OR of sensors)
    assert jan.clear_days == 31 and abs(jan.hot_frac - 3 / 31) < 1e-9
    assert a[a.month == pd.Period("2020-02", "M")].hot_days.isna().all()


def _act(values, start="2020-01"):
    idx = pd.period_range(start, periods=len(values), freq="M")
    return pd.DataFrame({"site_id": "A", "month": idx, "hot_frac": values})


def test_robust_case_detects_shutdown_point_in_time():
    act = _act([0.5] * 12 + [0.1, 0.1, 0.1])       # quarter 2021Q1 is a shutdown
    q = pd.period_range("2020Q1", "2021Q1", freq="Q")
    rep = pd.DataFrame({"site_id": "A", "period_start": q.start_time, "period_end": q.end_time.normalize(),
                        "cu_kt": [30, 30, 30, 30, 6.0],
                        "published_at": (q.end_time.normalize() + pd.Timedelta(days=20)).tz_localize("UTC")})
    c = P.robust_cases(act, rep, [_Site("A")])
    last = c.iloc[-1]
    assert abs(last.r - 0.2) < 1e-9 and abs(last.naive_kt - 30.0) < 0.5  # median quarterly rate (day-count)
    t = P.evaluate(c)
    assert abs(t.iloc[-1].sat_kt - 6.0) < 0.1 and abs(t.iloc[-1].err_naive_pct - 400.0) < 5


def test_norm_rate_ignores_unpublished_figures():
    q = pd.period_range("2020Q1", "2020Q4", freq="Q")
    rep = pd.DataFrame({"site_id": "A", "period_start": q.start_time, "period_end": q.end_time.normalize(),
                        "cu_kt": [30, 30, 30, 90.0],
                        "published_at": pd.to_datetime(["2020-04-20", "2020-07-20", "2020-10-20", "2021-06-01"], utc=True)})
    rate = P._norm_rate(rep, pd.Timestamp("2021-01-02", tz="UTC"), pd.Period("2021-01", "M"), 12)
    assert abs(rate - 30 / (91 / 30.4375)) < 0.5  # Q4 (90 kt) not yet published
