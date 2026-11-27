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


def _so2(values, start="2022-01", t=10.0, lag_days=5):
    per = pd.period_range(start, periods=len(values), freq="M")
    return pd.DataFrame({"site_id": "A", "period": per.astype(str), "E_kt_yr": values, "t_stat": t,
                         "available_at": (per.end_time.normalize() + pd.Timedelta(days=lag_days)).tz_localize("UTC")})


def test_so2_ratio_year_on_year_and_point_in_time():
    so2 = _so2([100.0] * 12 + [80.0] * 3)               # 2023 Q1 emissions 20 % below 2022 Q1
    dec = pd.Timestamp("2023-04-10", tz="UTC")
    r = P.so2_ratio(so2, "A", pd.Period("2023-01", "M"), pd.Period("2023-03", "M"), dec, mode="yoy")
    assert abs(r - 0.8) < 1e-9
    early = pd.Timestamp("2023-04-01", tz="UTC")       # March estimate not yet available -> only Jan, Feb
    assert abs(P.so2_ratio(so2, "A", pd.Period("2023-01", "M"), pd.Period("2023-03", "M"), early) - 0.8) < 1e-9
    weak = _so2([100.0] * 15, t=2.0)                    # insignificant fits are ignored
    assert np.isnan(P.so2_ratio(weak, "A", pd.Period("2023-01", "M"), pd.Period("2023-03", "M"), dec))


def test_fused_factor_keeps_heat_shutdown_and_damps_so2():
    f = P.fused_factor([0.2, 1.0, 1.0, 1.0], [1.0, 0.8, np.nan, 3.0], cut=0.6, w=0.5)
    assert np.allclose(f, [0.2, 0.9, 1.0, 1.25])


def test_image_ratio_pools_months_and_needs_enough_images():
    import pandas as pd
    from orbitcu import production as P
    idx = pd.period_range("2024-01", "2025-03", freq="M")
    e = pd.DataFrame({"img_usable": 4, "img_hot": 4}, index=idx)
    e.loc[pd.Period("2025-01", "M"):, "img_hot"] = 1          # quarter mostly cold
    r, a_ref, n = P.image_ratio(e, pd.Period("2025-01", "M"), pd.Period("2025-03", "M"))
    assert n == 12 and a_ref == 1.0 and abs(r - 0.25) < 1e-9
    e.loc[pd.Period("2025-01", "M"):, "img_usable"] = 0        # all cloudy: no ratio
    assert P.image_ratio(e, pd.Period("2025-01", "M"), pd.Period("2025-03", "M"))[0] != P.image_ratio(e, pd.Period("2025-01", "M"), pd.Period("2025-03", "M"))[0]


def test_pit_fine_adjust_uses_only_past_cases():
    import numpy as np
    import pandas as pd
    from orbitcu import production as P
    n = 30
    d = pd.DataFrame({"decision_time": pd.date_range("2020-01-01", periods=n, freq="90D"),
                      "visible": True, "r": 1.0, "naive_kt": 100.0,
                      "r_img": np.r_[np.tile([0.8, 1.0, 1.25], 10)]})
    d["cu_kt"] = 100.0 * d["r_img"] ** 0.5                       # output follows images, beta 0.5
    t = P.pit_fine_adjust(d, min_train=15)
    assert t["beta"].iloc[:15].isna().all()                       # not enough past cases yet
    assert abs(t["beta"].iloc[-1] - 0.5) < 1e-6
    assert t["err_fine_pct"].iloc[15:].abs().max() < 1e-6


def test_pit_fine_adjust_waits_for_publication_and_skips_zero_output():
    n = 30
    d = pd.DataFrame({"decision_time": pd.date_range("2020-01-01", periods=n, freq="90D", tz="UTC"),
                      "visible": True, "r": 1.0, "naive_kt": 100.0,
                      "r_img": np.tile([0.8, 1.0, 1.25], 10)})
    d["cu_kt"] = 100.0 * d["r_img"] ** 0.5
    # every figure is published two years after its decision time: far fewer past cases
    d["published_at"] = d["decision_time"] + pd.Timedelta(days=730)
    t = P.pit_fine_adjust(d, min_train=15)
    first = t["beta"].first_valid_index()
    assert first is not None and first >= 15 + 8                 # ~8 quarters of publication lag
    d.loc[3, "cu_kt"] = 0.0                                       # zero output: no -inf in the fit
    t = P.pit_fine_adjust(d.drop(columns="published_at"), min_train=15)
    assert len(t) == n - 1 and np.isfinite(t["y"]).all()
