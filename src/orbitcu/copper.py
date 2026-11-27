"""From SO2 emission rates to copper output, with a point-in-time backtest.

Why a calibration is unavoidable
--------------------------------
Copper concentrate (mostly chalcopyrite CuFeS2) carries roughly as much
sulfur as copper by mass (about 30 % S for 25-30 % Cu), so smelting one tonne
of copper releases about 2 t of SO2 (S -> SO2 doubles the mass). Modern
smelters capture 95-99.9 % of it in sulfuric-acid plants; what TROPOMI sees is
the fugitive remainder::

    E_SO2 = 2 * r_S/Cu * (1 - eta) * Cu

``eta`` (capture efficiency) differs by site and is unknown, and the SO2 fit
itself carries a systematic factor (lifetime, plume width, air-mass factor;
about +/-50 %, Fioletov et al. 2020). Both are absorbed into one site factor

    k_site = E_SO2 / Cu      (kt SO2 per kt Cu),

so that ``Cu_hat = E_SO2 / k_site``. ``k_site`` is estimated two ways:

1. **reported** - ratio of satellite emissions to the output the operator
   published for earlier periods (ratio estimator sum(E)/sum(Cu)); only
   reports whose ``published_at`` precedes the decision time are used;
2. **capacity** - when no output is published: the site is assumed to have run
   at ``u0 = 80 %`` of nameplate capacity over its first ``anchor_months``
   months of data. Estimates are then *relative* (they track changes from that
   anchor), and are labelled as such.

Concentrate throughput (Aurubis reports only that) is converted with a copper
grade of 28 % (Aurubis' typical concentrate mix; documented assumption).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CONC_GRADE = 0.28
U0 = 0.80


def to_cu_kt(row) -> float:
    if row["metric"] == "concentrate":
        return row["value"] * CONC_GRADE
    return row["value"]


def load_reported(path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["period_start", "period_end", "published_at"])
    df["cu_kt"] = df.apply(to_cu_kt, axis=1)
    df["published_at"] = df["published_at"].dt.tz_localize("UTC")
    return df


def period_emission(em: pd.DataFrame, site_id: str, start, end) -> dict:
    """Mean emission rate (kt SO2/yr) over [start, end] from monthly fits.

    Months are weighted by their number of valid pixels; returns the emitted
    mass (kt) over the period and its standard error, plus the latest
    ``available_at`` of the months used.
    """
    m = em[(em["site_id"] == site_id)].copy()
    m["month"] = pd.PeriodIndex(m["period"], freq="M")
    sel = m[(m["month"] >= pd.Period(start, "M")) & (m["month"] <= pd.Period(end, "M"))]
    sel = sel[sel["E_kt_yr"].notna()]
    n_exp = len(pd.period_range(start, end, freq="M"))
    if sel.empty:
        return {"E_kt": np.nan, "E_se_kt": np.nan, "months": 0, "months_expected": n_exp, "available_at": pd.NaT}
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25 + 1 / 365.25
    rate = np.average(sel["E_kt_yr"], weights=sel["n_pixels"])
    w = sel["n_pixels"] / sel["n_pixels"].sum()
    se = float(np.sqrt(np.sum((w * sel["E_se_kt_yr"]) ** 2)))
    return {"E_kt": rate * years, "E_se_kt": se * years, "months": len(sel), "months_expected": n_exp,
            "available_at": sel["available_at"].max()}


def reported_vs_satellite(em: pd.DataFrame, rep: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in rep.itertuples(index=False):
        e = period_emission(em, r.site_id, r.period_start, r.period_end)
        rows.append({"site_id": r.site_id, "period_start": r.period_start, "period_end": r.period_end,
                     "cu_kt": r.cu_kt, "published_at": r.published_at, **e})
    return pd.DataFrame(rows)


def pit_backtest(pairs: pd.DataFrame, min_coverage: float = 0.45) -> pd.DataFrame:
    """Leave-future-out prediction of each reported period.

    For period P, ``k`` is computed from the reported periods published
    before P's satellite estimate becomes available (``available_at``) and
    ending before P starts. The prediction is ``E_P / k``.
    """
    ok = pairs[(pairs["months"] >= min_coverage * pairs["months_expected"]) & pairs["E_kt"].notna()]
    out = []
    for p in ok.itertuples(index=False):
        hist = ok[(ok["site_id"] == p.site_id) & (ok["published_at"] <= p.available_at)
                  & (ok["period_end"] < p.period_start)]
        if hist.empty or hist["cu_kt"].sum() <= 0:
            continue
        k = hist["E_kt"].sum() / hist["cu_kt"].sum()
        if k <= 0:
            continue
        pred = p.E_kt / k
        out.append({"site_id": p.site_id, "period_start": p.period_start, "period_end": p.period_end,
                    "decision_time": p.available_at, "n_calib": len(hist), "k": k,
                    "cu_reported": p.cu_kt, "cu_predicted": pred,
                    "cu_predicted_se": p.E_se_kt / k,
                    "error_pct": 100 * (pred - p.cu_kt) / p.cu_kt if p.cu_kt else np.nan})
    return pd.DataFrame(out)


def site_estimates(em: pd.DataFrame, sites, rep: pd.DataFrame | None = None, anchor_months: int = 12,
                   freq: str = "Q") -> pd.DataFrame:
    """Copper estimate per site and period (quarter by default).

    Uses the 'reported' calibration when the site has published output, the
    'capacity' anchor otherwise. ``detectable`` = annual-mean emission at
    least 3 standard errors above zero.

    ``k`` uses every reported pair (descriptive, in-sample). ``k_pit`` / ``cu_kt_pit`` use
    only the pairs that ended and were published before the period starts, so they are the
    figures that could have been computed at the time (NaN until such a pair exists).
    """
    rows = []
    for s in sites:
        m = em[em["site_id"] == s.id].copy()
        m = m[m["E_kt_yr"].notna()]
        if m.empty:
            continue
        m["month"] = pd.PeriodIndex(m["period"], freq="M")
        m = m.sort_values("month")
        mean_rate = np.average(m["E_kt_yr"], weights=m["n_pixels"])
        w = m["n_pixels"] / m["n_pixels"].sum()
        se_rate = float(np.sqrt(np.sum((w * m["E_se_kt_yr"]) ** 2)))
        detectable = mean_rate > 3 * se_rate and mean_rate > 0
        method, k = "none", np.nan
        pairs = None
        if rep is not None and (rep["site_id"] == s.id).any():
            pairs = reported_vs_satellite(em, rep[rep["site_id"] == s.id])
            pairs = pairs[pairs["E_kt"].notna() & (pairs["months"] >= 0.45 * pairs["months_expected"])]
            if not pairs.empty and pairs["cu_kt"].sum() > 0 and pairs["E_kt"].sum() > 0:
                k, method = pairs["E_kt"].sum() / pairs["cu_kt"].sum(), "reported"
        if method == "none" and s.capacity_kt:
            anchor = m[m["month"] < m["month"].iloc[0] + anchor_months]
            a_rate = np.average(anchor["E_kt_yr"], weights=anchor["n_pixels"])
            if a_rate > 0:
                k, method = a_rate / (U0 * s.capacity_kt), "capacity"
        per = m["month"].dt.asfreq(freq) if hasattr(m["month"], "dt") else m["month"].apply(lambda x: x.asfreq(freq))
        for p, g in m.groupby(per):
            rate = np.average(g["E_kt_yr"], weights=g["n_pixels"])
            k_pit = np.nan
            if method == "reported" and pairs is not None:
                t0 = p.start_time
                pub = pd.to_datetime(pairs["published_at"], errors="coerce", utc=True).dt.tz_localize(None)
                pend = pd.to_datetime(pairs["period_end"])
                past = pairs[(pend < t0) & pub.notna() & (pub < t0)]
                if not past.empty and past["cu_kt"].sum() > 0 and past["E_kt"].sum() > 0:
                    k_pit = past["E_kt"].sum() / past["cu_kt"].sum()
            ww = g["n_pixels"] / g["n_pixels"].sum()
            se = float(np.sqrt(np.sum((ww * g["E_se_kt_yr"]) ** 2)))
            frac = len(pd.period_range(p.start_time, p.end_time, freq="M")) / 12
            rows.append({"site_id": s.id, "period": str(p), "E_kt_yr": rate, "E_se_kt_yr": se,
                         "months": len(g), "detectable_site": detectable, "method": method, "k": k,
                         "cu_kt": rate * frac / k if k == k else np.nan,
                         "cu_se_kt": se * frac / k if k == k else np.nan,
                         "cu_kt_annualised": rate / k if k == k else np.nan,
                         "k_pit": k_pit,
                         "cu_kt_pit": rate * frac / k_pit if k_pit == k_pit else np.nan,
                         "capacity_kt": s.capacity_kt, "available_at": g["available_at"].max()})
    return pd.DataFrame(rows)
