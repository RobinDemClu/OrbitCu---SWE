"""Copper output estimates from satellite activity, with a point-in-time test.

Idea
----
Satellites see *activity* (heat at the furnace, SO2 downwind), not tonnes.
What they can tell reliably is how much a smelter is running **compared with
its own recent past**: a furnace shut for maintenance disappears from VIIRS
and its SO2 plume fades. So the estimate is built as

    Cu_hat(P) = Cu_ref * g(r_P),        r_P = A_P / A_ref,
    g(r)      = clip(1 + beta * (r - 1), 0, g_max)

* ``A_P``   - satellite activity of the site during period P (VIIRS hot days
  per month, both satellites, within 1.5 km of the furnace);
* ``A_ref`` - the same activity over the 12 months before P (known at the
  decision time: no future information);
* ``Cu_ref`` - the output rate in the reference window: the operator's
  published figures when they exist (only those published before the decision
  time), otherwise ``capacity * U0``;
* ``beta``  - how strongly output follows activity, pooled over all sites
  with published output and fitted only on periods before P.

The test compares this estimate with the naive forecast ``Cu_ref`` (output
continues at its recent rate). Satellites add value only if they beat it,
mainly during shutdowns and restarts. Each published figure is predicted at
its decision time = end of period + satellite latency (VIIRS: a few hours).

Truth entities may cover several sites (KGHM reports Glogow and Legnica
together): their activity is the capacity-weighted sum of the sites'.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .geolocate import haversine_km

U0 = 0.80
G_MAX = 1.3

# reported entity -> sites whose activity it covers
ENTITY_SITES = {
    "PL-KGHM": ["PL-GLOGOW", "PL-LEGNICA"],
}


# ------------------------------------------------------------------ activity
def viirs_monthly(df: pd.DataFrame, lat: float, lon: float, radius_km: float = 1.5,
                  start: str | None = None, end: str | None = None,
                  clear: pd.Series | None = None) -> pd.DataFrame:
    """Per month: distinct hot days, detections and summed FRP near (lat, lon).

    With ``clear`` (daily clear-sky flags from :func:`clear_days`), also the
    number of clear days and of hot days among them; ``hot_frac`` =
    hot clear days / clear days is the activity measure corrected for clouds.
    """
    idx = pd.period_range(start, end, freq="M")
    near = df
    if not df.empty:
        near = df[haversine_km(df["latitude"].values, df["longitude"].values, lat, lon) <= radius_km]
    hot = pd.Series(dtype=bool)
    if len(near):
        days = pd.to_datetime(near["acq_date"])
        g = near.assign(day=days, month=days.dt.to_period("M")).groupby("month")
        out = pd.DataFrame({"hot_days": g["day"].nunique(), "n_det": g.size(), "frp_sum": g["frp"].sum()})
        hot = pd.Series(True, index=pd.DatetimeIndex(days.dt.normalize().unique()))
    else:
        out = pd.DataFrame(columns=["hot_days", "n_det", "frp_sum"])
    out = out.reindex(idx, fill_value=0).astype(float)
    if clear is not None:
        c = clear[clear]  # clear days
        hc = c.index.isin(hot.index)
        cm = pd.Series(1.0, index=c.index).groupby(c.index.to_period("M")).sum()
        hm = pd.Series(hc.astype(float), index=c.index).groupby(c.index.to_period("M")).sum()
        out["clear_days"] = cm.reindex(idx).fillna(0.0)
        out["hot_clear_days"] = hm.reindex(idx).fillna(0.0)
        out["hot_frac"] = out["hot_clear_days"] / out["clear_days"].where(out["clear_days"] >= 3)
    return out


def clear_days(tcc: pd.DataFrame, lon: float, max_cloud: float = 0.5) -> pd.Series:
    """Daily flag: was the sky clear at a VIIRS overpass (~01:30 or ~13:30 local solar time)?

    ``tcc``: hourly ERA5 total cloud cover at the site (column ``tcc``, 0-1).
    A day is clear if the cloud cover at the night or the day overpass hour is
    below ``max_cloud``. Days are counted in UTC dates, like FIRMS ``acq_date``.
    """
    t = tcc[["valid_time", "tcc"]].copy()
    t["valid_time"] = pd.to_datetime(t["valid_time"], utc=True)
    local = (t["valid_time"] + pd.to_timedelta(lon / 15.0, "h")).dt.hour
    t = t[local.isin([1, 2, 13, 14])]
    t["day"] = t["valid_time"].dt.tz_convert(None).dt.normalize()
    return t.groupby("day")["tcc"].min() < max_cloud


def clear_monthly(tcc: pd.DataFrame, lon: float, start: str, end: str, max_cloud: float = 0.5) -> pd.Series:
    c = clear_days(tcc, lon, max_cloud)
    m = c.groupby(c.index.to_period("M")).sum().astype(float)
    return m.reindex(pd.period_range(start, end, freq="M"))


def site_activity(firms_dirs: list, sites, start: str, end: str, radius_km: float = 1.5,
                  tcc_dir=None) -> pd.DataFrame:
    """Long table (site_id, month, hot_days, n_det, frp_sum) from cached VIIRS months.

    ``firms_dirs``: one cache folder per satellite (``<dir>/<site_id>/<YYYY-MM>.parquet``);
    detections of all satellites are pooled (a day is hot if any satellite saw heat).
    A month missing from a satellite's cache is NaN, not zero.
    """
    from pathlib import Path

    rows = []
    for s in sites:
        parts, have = [], set()
        for d in firms_dirs:
            for f in sorted((Path(d) / s.id).glob("*.parquet")):
                have.add(f.stem)
                p = pd.read_parquet(f)
                if not p.empty:
                    parts.append(p)
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        clear = None
        if tcc_dir is not None:
            f = sorted(Path(tcc_dir).glob(f"{s.id}_*.parquet"))
            if f:
                clear = clear_days(pd.read_parquet(f[0]), s.lon)
        m = viirs_monthly(df, s.lat, s.lon, radius_km, start, end, clear)
        m.loc[[p for p in m.index if str(p) not in have]] = np.nan
        m["site_id"] = s.id
        rows.append(m.rename_axis("month").reset_index())
    return pd.concat(rows, ignore_index=True)


def entity_activity(act: pd.DataFrame, sites, entity: str, col: str = "hot_days") -> pd.Series:
    """Monthly activity of a reported entity (capacity-weighted mean over its sites)."""
    ids = ENTITY_SITES.get(entity, [entity])
    cap = {s.id: (s.capacity_kt or 1.0) for s in sites}
    a = act[act["site_id"].isin(ids)].pivot(index="month", columns="site_id", values=col)
    w = pd.Series({i: cap.get(i, 1.0) for i in a.columns})
    return (a * w).sum(axis=1, min_count=len(a.columns)) / w.sum()


# --------------------------------------------------------------------- model
@dataclass
class Config:
    ref_months: int = 12
    col: str = "hot_days"
    min_ref_activity: float = 1.0    # below this the site is invisible: no satellite signal
    latency_days: int = 1


def _ratio(a: pd.Series, p0: pd.Period, p1: pd.Period, ref_months: int) -> tuple[float, float]:
    cur = a.loc[p0:p1]
    ref = a.loc[p0 - ref_months:p0 - 1]
    if cur.isna().any() or len(cur) == 0 or ref.notna().sum() < ref_months * 0.75:
        return np.nan, np.nan
    return float(cur.mean()), float(ref.mean())


def _rate(rep: pd.DataFrame, entity: str, before: pd.Timestamp, p0: pd.Period, months: int) -> float:
    """Published output rate (kt per month) over the reference window, PIT."""
    r = rep[(rep["site_id"] == entity) & (rep["published_at"] <= before)]
    if r.empty:
        return np.nan
    w0 = (p0 - months).start_time
    r = r[(r["period_end"] >= w0) & (r["period_end"] < p0.start_time)]
    if r.empty:
        return np.nan
    m = ((r["period_end"] - r["period_start"]).dt.days + 1) / 30.4375
    return float(r["cu_kt"].sum() / m.sum())


def build_cases(act: pd.DataFrame, rep: pd.DataFrame, sites, cfg: Config = Config()) -> pd.DataFrame:
    """One row per published figure: activity ratio, reference rate, truth."""
    rows = []
    for e in rep["site_id"].unique():
        a = entity_activity(act, sites, e, cfg.col)
        if a.empty:
            continue
        for r in rep[rep["site_id"] == e].itertuples(index=False):
            p0, p1 = pd.Period(r.period_start, "M"), pd.Period(r.period_end, "M")
            n_m = len(pd.period_range(p0, p1, freq="M"))
            decision = pd.Timestamp(r.period_end, tz="UTC") + pd.Timedelta(days=cfg.latency_days)
            a_cur, a_ref = _ratio(a, p0, p1, cfg.ref_months)
            ref_rate = _rate(rep, e, decision, p0, cfg.ref_months)
            rows.append({"entity": e, "period_start": r.period_start, "period_end": r.period_end,
                         "months": n_m, "decision_time": decision, "cu_kt": r.cu_kt,
                         "a_cur": a_cur, "a_ref": a_ref, "ref_rate_kt_m": ref_rate,
                         "naive_kt": ref_rate * n_m})
    c = pd.DataFrame(rows)
    c["r"] = c["a_cur"] / c["a_ref"]
    c["visible"] = c["a_ref"] >= cfg.min_ref_activity
    c["y"] = c["cu_kt"] / c["naive_kt"]   # output relative to its recent rate
    return c


def fit_beta(train: pd.DataFrame) -> float:
    """Least squares slope of (y - 1) on (r - 1), through the origin."""
    t = train[train["visible"] & train["r"].notna() & train["y"].notna()]
    if len(t) < 4:
        return np.nan
    x, y = t["r"].clip(0, 3) - 1, t["y"] - 1
    # monthly and quarterly figures: weight by length so quarters count more
    w = t["months"]
    den = float((w * x * x).sum())
    return float((w * x * y).sum() / den) if den > 0 else np.nan


def pit_test(cases: pd.DataFrame, min_train: int = 8) -> pd.DataFrame:
    """Predict each figure with beta fitted on figures published before its decision time."""
    out = []
    pub = cases.assign(published=cases["decision_time"])  # truth known after the period
    for c in cases.sort_values("decision_time").itertuples(index=False):
        if not (c.naive_kt == c.naive_kt) or not c.visible or not (c.r == c.r):
            continue
        # training: figures whose period ended (and truth published ~20 days later) before this decision
        train = pub[pub["period_end"] + pd.Timedelta(days=30) < pd.Timestamp(c.decision_time).tz_convert(None)]
        if len(train[train["visible"] & train["r"].notna()]) < min_train:
            continue
        b = fit_beta(train)
        if b != b:
            continue
        g = float(np.clip(1 + b * (min(c.r, 3) - 1), 0, G_MAX))
        out.append({"entity": c.entity, "period_start": c.period_start, "period_end": c.period_end,
                    "months": c.months, "decision_time": c.decision_time, "cu_kt": c.cu_kt,
                    "naive_kt": c.naive_kt, "sat_kt": c.naive_kt * g, "beta": b, "r": c.r,
                    "err_naive_pct": 100 * (c.naive_kt - c.cu_kt) / c.cu_kt,
                    "err_sat_pct": 100 * (c.naive_kt * g - c.cu_kt) / c.cu_kt})
    return pd.DataFrame(out)


def summarise(test: pd.DataFrame) -> pd.DataFrame:
    def f(g):
        return pd.Series({"n": len(g),
                          "mae_naive_pct": g["err_naive_pct"].abs().mean(),
                          "mae_sat_pct": g["err_sat_pct"].abs().mean(),
                          "bias_sat_pct": g["err_sat_pct"].mean()})
    return test.groupby("entity").apply(f, include_groups=False)


# ------------------------------------------------- robust model (shutdown rule)
# The linear model above, fitted point-in-time, gives a small beta: month-to-month
# changes in VIIRS activity are mostly noise (clouds, overpass sampling), while
# output is steady. The useful signal is the large drop during a shutdown, when the
# furnace stops emitting heat. The robust model therefore keeps the recent output
# rate unless the activity falls below a fixed fraction of its usual level:
#
#     Cu_hat(P) = R_norm * s(r_P),   s(r) = r if r < CUT else 1,
#     r_P       = A_P / median(A over the 12 months before P),
#     R_norm    = median of the published monthly rates of the periods that ended
#                 in the 12 months before P (published before the decision time),
#                 else the latest published figure, else capacity * U0.
#
# Medians, not means, so that a past shutdown does not pull down the normal level.
# CUT is a fixed physical rule (a furnace at < 60 % of its usual heat is largely
# stopped); it is not fitted. Sensitivity to CUT is reported separately.

CUT = 0.6


def activity_from_masks(masks: pd.DataFrame, sites, tcc_dir, start: str, end: str,
                        nodata: set[str] = frozenset(), max_cloud: float = 0.5) -> pd.DataFrame:
    """Monthly activity from compact day masks.

    ``masks``: columns site_id, month (YYYY-MM), mask (hex, bit d-1 = a VIIRS
    detection within the site radius on UTC day d), n_det, frp_sum; one or more
    sensors (rows of the same site-month are OR-ed / summed). Months in ``nodata``
    are NaN (no satellite archive). Returns the same long table as
    :func:`site_activity`, with the cloud-corrected ``hot_frac``.
    """
    from pathlib import Path

    idx = pd.period_range(start, end, freq="M")
    rows = []
    for s in sites:
        m = masks[masks["site_id"] == s.id]
        days = []
        for r in m.itertuples(index=False):
            p, bits = pd.Period(r.month, "M"), int(str(r.mask), 16)
            days += [p.start_time + pd.Timedelta(days=d) for d in range(p.days_in_month) if bits >> d & 1]
        hot = pd.DatetimeIndex(sorted(set(days)))
        g = m.assign(month=pd.PeriodIndex(m["month"], freq="M")).groupby("month")
        a = pd.DataFrame(index=idx)
        a["hot_days"] = pd.Series(1.0, index=hot).groupby(hot.to_period("M")).sum().reindex(idx).fillna(0.0)
        a["n_det"] = g["n_det"].sum().reindex(idx).fillna(0.0)
        a["frp_sum"] = g["frp_sum"].sum().reindex(idx).fillna(0.0)
        f = sorted(Path(tcc_dir).glob(f"{s.id}_*.parquet")) if tcc_dir is not None else []
        if f:
            c = clear_days(pd.read_parquet(f[0]), s.lon, max_cloud)
            c = c[c]
            hc = pd.Series(c.index.isin(hot).astype(float), index=c.index)
            a["clear_days"] = pd.Series(1.0, index=c.index).groupby(c.index.to_period("M")).sum().reindex(idx).fillna(0.0)
            a["hot_clear_days"] = hc.groupby(c.index.to_period("M")).sum().reindex(idx).fillna(0.0)
            a["hot_frac"] = a["hot_clear_days"] / a["clear_days"].where(a["clear_days"] >= 3)
        a.loc[[p for p in idx if str(p) in nodata]] = np.nan
        a["site_id"] = s.id
        rows.append(a.rename_axis("month").reset_index())
    return pd.concat(rows, ignore_index=True)


def shutdown_factor(r, cut: float = CUT):
    r = np.asarray(r, dtype=float)
    return np.where(r < cut, np.clip(r, 0, None), 1.0)


def _norm_rate(R: pd.DataFrame, decision: pd.Timestamp, p0: pd.Period, months: int) -> float:
    """Median published monthly rate of the periods that ended in the window (PIT)."""
    past = R[(R["published_at"] <= decision) & (R["period_end"] < p0.start_time)]
    win = past[past["period_end"] >= (p0 - months).start_time]
    if win.empty:
        win = past.sort_values("period_end").tail(1)
    if win.empty:
        return np.nan
    m = ((win["period_end"] - win["period_start"]).dt.days + 1) / 30.4375
    return float((win["cu_kt"] / m).median())


def robust_cases(act: pd.DataFrame, rep: pd.DataFrame, sites, col: str = "hot_frac",
                 ref_months: int = 12, min_ref: float = 0.05, latency_days: int = 1) -> pd.DataFrame:
    """One row per published figure: robust activity ratio and normal published rate."""
    rows = []
    for e in rep["site_id"].unique():
        a = entity_activity(act, sites, e, col)
        R = rep[rep["site_id"] == e]
        for r in R.itertuples(index=False):
            p0, p1 = pd.Period(r.period_start, "M"), pd.Period(r.period_end, "M")
            n = len(pd.period_range(p0, p1, freq="M"))
            decision = pd.Timestamp(r.period_end, tz="UTC") + pd.Timedelta(days=latency_days)
            cur, ref = a.loc[p0:p1], a.loc[p0 - ref_months:p0 - 1]
            ok = len(cur) and not cur.isna().any() and ref.notna().sum() >= ref_months * 0.75
            a_ref = float(ref.median()) if ok else np.nan
            rate = _norm_rate(R, decision, p0, ref_months)
            rows.append({"entity": e, "period_start": r.period_start, "period_end": r.period_end,
                         "months": n, "decision_time": decision, "cu_kt": r.cu_kt,
                         "a_cur": float(cur.mean()) if ok else np.nan, "a_ref": a_ref,
                         "naive_kt": rate * n})
    c = pd.DataFrame(rows)
    c["r"] = c["a_cur"] / c["a_ref"].where(c["a_ref"] > 0)
    c["visible"] = c["a_ref"] >= min_ref
    return c


def evaluate(cases: pd.DataFrame, cut: float = CUT) -> pd.DataFrame:
    """Errors of the naive rate and of the satellite estimate on testable figures."""
    t = cases[cases["visible"] & cases["r"].notna() & (cases["naive_kt"] > 0)].copy()
    t["sat_kt"] = t["naive_kt"] * shutdown_factor(t["r"], cut)
    t["err_naive_pct"] = 100 * (t["naive_kt"] - t["cu_kt"]) / t["cu_kt"]
    t["err_sat_pct"] = 100 * (t["sat_kt"] - t["cu_kt"]) / t["cu_kt"]
    return t


# --- SO2 + heat fusion (old smelters without full sulphur capture) ---------------------------
# Heat (VIIRS) says whether the furnaces run; it saturates when a plant is busy and cannot see a
# 20 % slowdown.  SO2 (TROPOMI, Fioletov EMG fit, see emissions.py) scales with the sulphur
# burnt, so at plants that vent their gas it carries the partial changes.  The fusion keeps the
# heat shutdown rule and, when the plant runs, nudges the normal rate by a damped SO2 ratio.
SO2_W = 0.5          # weight on the SO2 ratio (0 = heat only, 1 = take the SO2 ratio at face value)
SO2_MIN_T = 4.0      # a month is used only if the EMG fit is significant (t >= 4)


def so2_ratio(so2: pd.DataFrame, site_id: str, p0: pd.Period, p1: pd.Period,
              decision: pd.Timestamp, mode: str = "yoy", ref_months: int = 12, min_cur: int = 2,
              min_ref: int = 3, min_t: float = SO2_MIN_T) -> float:
    """SO2 activity ratio of months p0..p1.

    mode="yoy": mean rate over the months of p0..p1 divided by the mean rate over the SAME
    calendar months one year earlier (only months present in both years).  This cancels the
    season (Norilsk is dark from October to February) and slow changes in the background.
    mode="trailing": mean rate divided by the median of the ref_months before p0.
    Only months available at the decision time with a significant fit (t >= min_t) count."""
    d = so2[(so2["site_id"] == site_id) & (so2["t_stat"] >= min_t)].copy()
    if d.empty:
        return np.nan
    av = pd.to_datetime(d["available_at"], utc=True, format="mixed")
    d = d[av <= decision]
    e = pd.Series(d["E_kt_yr"].to_numpy(), index=pd.PeriodIndex(d["period"], freq="M"))
    e = e[~e.index.duplicated()]
    months = pd.period_range(p0, p1, freq="M")
    if mode == "yoy":
        pairs = [(m, m - 12) for m in months if m in e.index and (m - 12) in e.index]
        if len(pairs) < min_cur:
            return np.nan
        prev = np.mean([e[b] for _, b in pairs])
        return float(np.mean([e[a] for a, _ in pairs]) / prev) if prev > 0 else np.nan
    cur = e[(e.index >= p0) & (e.index <= p1)]
    ref = e[(e.index >= p0 - ref_months) & (e.index < p0)]
    if len(cur) < min_cur or len(ref) < min_ref or ref.median() <= 0:
        return np.nan
    return float(cur.mean() / ref.median())


def fused_factor(r_heat, r_so2, cut: float = CUT, w: float = SO2_W, lo: float = 0.5, hi: float = 1.5):
    """Heat shutdown rule first; if the plant runs and an SO2 ratio exists, scale by
    1 + w (clip(r_so2) - 1)."""
    f = shutdown_factor(np.asarray(r_heat, dtype=float), cut)
    rs = np.asarray(r_so2, dtype=float)
    adj = 1.0 + w * (np.clip(rs, lo, hi) - 1.0)
    return np.where((f == 1.0) & np.isfinite(rs), adj, f)


def _prev_year_figure(rep: pd.DataFrame, entity: str, p_start, p_end, decision) -> float:
    """Published output of the same period one year earlier, known at the decision time."""
    R = rep[(rep["site_id"] == entity)]
    s0 = pd.Timestamp(p_start) - pd.DateOffset(years=1); s1 = pd.Timestamp(p_end) - pd.DateOffset(years=1)
    m = R[(R["period_start"] == s0) & (R["period_end"].dt.normalize() == s1.normalize())
          & (R["published_at"] < decision)]
    return float(m["cu_kt"].iloc[-1]) if len(m) else np.nan


def evaluate_fusion(test: pd.DataFrame, so2: pd.DataFrame, rep: pd.DataFrame, site_of: dict,
                    cut: float = CUT, w: float = SO2_W) -> pd.DataFrame:
    """Add the SO2 + heat estimate to an evaluate() table.

    For entities in site_of (old smelters whose SO2 plume is seen):
      heat says shut (r < cut)       -> heat estimate (naive x r)
      else, SO2 year-on-year ratio and last year's figure for the same period both known
                                     -> last_year x (1 + w (r_so2_yoy - 1))
      else                           -> heat estimate.
    Other entities keep the heat estimate."""
    t = test.copy()
    rs, base = [], []
    for c in t.itertuples(index=False):
        sid = site_of.get(c.entity)
        if sid is None:
            rs.append(np.nan); base.append(np.nan); continue
        p0 = pd.Timestamp(c.period_start).to_period("M"); p1 = pd.Timestamp(c.period_end).to_period("M")
        dec = pd.Timestamp(c.decision_time)
        rs.append(so2_ratio(so2, sid, p0, p1, dec, mode="yoy"))
        base.append(_prev_year_figure(rep, c.entity, c.period_start, c.period_end, dec))
    t["r_so2"] = rs
    t["last_year_kt"] = base
    shut = np.asarray(t["r"], dtype=float) < cut
    ok = np.isfinite(t["r_so2"]) & np.isfinite(t["last_year_kt"]) & ~shut
    adj = 1.0 + w * (np.clip(t["r_so2"], 0.5, 1.5) - 1.0)
    t["fused_kt"] = np.where(ok, t["last_year_kt"] * adj, t["sat_kt"])
    t["so2_used"] = ok
    t["err_fused_pct"] = 100 * (t["fused_kt"] - t["cu_kt"]) / t["cu_kt"]
    return t


def estimate_sites(act: pd.DataFrame, rep: pd.DataFrame, sites, col: str = "hot_frac",
                   start: str = "2019Q1", end: str = "2026Q3", cut: float = CUT,
                   min_ref: float = 0.05, start_dates: dict | None = None) -> pd.DataFrame:
    """Quarterly copper estimate per site, point-in-time.

    Sites inside a reported entity get the entity's normal rate split by capacity;
    sites without published output get ``capacity * U0``. When the site is too dim
    for VIIRS (median activity < ``min_ref``) the estimate is the normal rate and
    flagged ``visible=False``. ``start_dates`` (site -> first production date)
    sets the estimate to 0 before a plant started.
    """
    start_dates = start_dates or {}
    ent_of = {s: e for e, ss in ENTITY_SITES.items() for s in ss}
    cap = {s.id: s.capacity_kt for s in sites}
    rows = []
    for s in sites:
        e = ent_of.get(s.id, s.id)
        R = rep[rep["site_id"] == e]
        share = 1.0
        if e in ENTITY_SITES:
            tot = sum(cap.get(i) or 0 for i in ENTITY_SITES[e])
            share = (cap.get(s.id) or 0) / tot if tot else 1 / len(ENTITY_SITES[e])
        a = act[act["site_id"] == s.id].set_index("month")[col]
        for q in pd.period_range(start, end, freq="Q"):
            p0, p1 = q.asfreq("M", "s"), q.asfreq("M", "e")
            decision = q.end_time.tz_localize("UTC").normalize() + pd.Timedelta(days=1)
            rate = _norm_rate(R, decision, p0, 12) * share if not R.empty else np.nan
            basis = "published"
            if not rate == rate:
                rate, basis = (cap.get(s.id) or np.nan) * U0 / 12, "capacity"
            cur, ref = a.loc[p0:p1], a.loc[p0 - 12:p0 - 1]
            a_ref = float(ref.median()) if ref.notna().sum() >= 9 else np.nan
            vis = a_ref == a_ref and a_ref >= min_ref and not cur.isna().any()
            r = float(cur.mean()) / a_ref if vis and a_ref > 0 else np.nan
            f = float(shutdown_factor([r], cut)[0]) if vis else 1.0
            if s.id in start_dates and q.start_time < pd.Timestamp(start_dates[s.id]):
                f, basis = 0.0, "not_started"
            rows.append({"site_id": s.id, "quarter": str(q), "rate_basis": basis, "visible": vis,
                         "r": r, "factor": f, "normal_kt": rate * 3, "est_kt": rate * 3 * f})
    return pd.DataFrame(rows)
