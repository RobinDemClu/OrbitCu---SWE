"""Weekly world smelter activity index and dated event list.

The copper estimate (production.py) showed that heat seen from space is reliable for one
thing: whether a smelter runs, stops or restarts. This module turns that into

* a weekly status per smelter (running / stopped / unknown), point-in-time;
* a capacity-weighted index: share of the watched capacity that is running;
* a list of dated events (stop, restart, start-up) with the first day the satellite data
  showed them.

Inputs are the compact VIIRS day masks (site, month, hex mask) and ERA5 hourly cloud cover
per site, the same as ``production.activity_from_masks``.

Status rule (fixed in advance, same threshold as the copper estimate):
  for each week ending day t, activity = hot clear days / clear days over the 56 days to t;
  reference (default "seasonal") = median activity of the same calendar weeks (+-2) in the
  three previous years, so what repeats every year (seasonal cloud artefacts, planned annual
  maintenance) is not flagged; "trailing" = median over the previous 52 weeks, ending
  4 weeks before t so a stop does not lower its own reference;
  r = activity / reference;  stopped if r < 0.6, running otherwise.
  unknown if fewer than ``min_clear`` clear days in the window or if the site is too dim
  (reference < ``min_ref``); an unknown week keeps the last known status for up to
  ``carry_weeks`` weeks.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .production import CUT, clear_days, read_tcc

WINDOW_DAYS = 56   # 8 weeks: fewer cloud-driven flips than 4 weeks
REF_WEEKS = 52
GAP_WEEKS = 4


def daily_flags(masks: pd.DataFrame, sites, tcc_dir, start: str, end: str,
                max_cloud: float = 0.5, nodata_before: str | None = None) -> pd.DataFrame:
    """One row per site and UTC day: ``hot`` (VIIRS detection within the site radius) and
    ``clear`` (cloud cover below ``max_cloud`` at an overpass hour). Days before
    ``nodata_before`` (no satellite archive) are dropped."""
    days = pd.date_range(start, end, freq="D")
    if nodata_before is not None:
        days = days[days >= pd.Timestamp(nodata_before)]
    out = []
    for s in sites:
        m = masks[masks["site_id"] == s.id]
        hot = set()
        for r in m.itertuples(index=False):
            p, bits = pd.Period(r.month, "M"), int(str(r.mask), 16)
            hot.update(p.start_time + pd.Timedelta(days=d) for d in range(p.days_in_month) if bits >> d & 1)
        t = read_tcc(tcc_dir, s.id)
        if t is None:
            continue
        c = clear_days(t, s.lon, max_cloud).reindex(days)
        out.append(pd.DataFrame({"site_id": s.id, "day": days,
                                 "hot": days.isin(sorted(hot)), "clear": c.fillna(False).to_numpy(bool),
                                 "has_cloud": c.notna().to_numpy()}))
    return pd.concat(out, ignore_index=True)


def _seasonal_ref(a: pd.Series, years: int = 3, half_width: int = 2) -> pd.Series:
    """Median activity of the same calendar weeks (+-half_width) in the previous ``years``
    years. Removes what repeats every year: seasonal cloud artefacts (coastal fog at Ilo,
    winter overcast) and planned annual maintenance, which the market already knows."""
    lags = [52 * y + k for y in range(1, years + 1) for k in range(-half_width, half_width + 1)]
    m = pd.concat([a.shift(l) for l in lags], axis=1)
    return m.median(axis=1).where(m.notna().sum(axis=1) >= 2 * half_width + 1)


def weekly_status(daily: pd.DataFrame, cut: float = CUT, min_clear: int = 5, min_ref: float = 0.3,
                  carry_weeks: int = 4, week_end: str = "W-SUN", reference: str = "seasonal") -> pd.DataFrame:
    """Weekly point-in-time status per site (see module docstring)."""
    rows = []
    for sid, d in daily.groupby("site_id"):
        d = d.set_index("day").sort_index()
        hc = (d["hot"] & d["clear"]).astype(float)
        cl = d["clear"].astype(float)
        n_hc = hc.rolling(f"{WINDOW_DAYS}D").sum()
        n_cl = cl.rolling(f"{WINDOW_DAYS}D").sum()
        weeks = pd.date_range(d.index[0] + pd.Timedelta(days=WINDOW_DAYS - 1), d.index[-1], freq=week_end)
        a = (n_hc / n_cl.where(n_cl >= min_clear)).reindex(weeks)
        ncl = n_cl.reindex(weeks)
        if reference == "seasonal":
            ref = _seasonal_ref(a)
        else:
            ref = a.shift(GAP_WEEKS).rolling(REF_WEEKS, min_periods=int(REF_WEEKS * 0.6)).median()
        r = a / ref.where(ref > 0)
        visible = ref >= min_ref
        st = pd.Series(np.where(~visible | r.isna(), "unknown", np.where(r < cut, "stopped", "running")),
                       index=weeks)
        # carry the last known status over short gaps (clouds)
        known = st.where(st != "unknown")
        filled = known.ffill(limit=carry_weeks)
        rows.append(pd.DataFrame({"site_id": sid, "week_end": weeks, "activity": a.to_numpy(),
                                  "clear_days": ncl.to_numpy(), "ref": ref.to_numpy(), "r": r.to_numpy(),
                                  "visible": visible.to_numpy(), "status_raw": st.to_numpy(),
                                  "status": filled.fillna("unknown").to_numpy()}))
    return pd.concat(rows, ignore_index=True)


def capacity_index(status: pd.DataFrame, sites, start_dates: dict | None = None) -> pd.DataFrame:
    """Weekly capacity-weighted index.

    running_share = running capacity / capacity with a known status.
    running_kt    = running capacity in kt/yr (known sites only).
    coverage      = capacity with a known status / total watched capacity.
    Sites with a ``start_dates`` entry count as stopped (known) before that date, so a
    start-up raises the index instead of entering it silently."""
    cap = {s.id: float(s.capacity_kt or 0) for s in sites}
    start_dates = start_dates or {}
    s = status.copy()
    s["cap"] = s["site_id"].map(cap).fillna(0.0)
    for sid, d0 in start_dates.items():
        pre = (s["site_id"] == sid) & (s["week_end"] < pd.Timestamp(d0))
        s.loc[pre, "status"] = "stopped"
    known = s["status"] != "unknown"
    s["cap_known"] = s["cap"] * known
    s["cap_run"] = s["cap"] * (s["status"] == "running")
    g = s.groupby("week_end")
    out = pd.DataFrame({"running_kt": g["cap_run"].sum(), "known_kt": g["cap_known"].sum(),
                        "total_kt": g["cap"].sum(),
                        "n_running": g["status"].apply(lambda x: (x == "running").sum()),
                        "n_stopped": g["status"].apply(lambda x: (x == "stopped").sum()),
                        "n_unknown": g["status"].apply(lambda x: (x == "unknown").sum())})
    out["running_share"] = out["running_kt"] / out["known_kt"].where(out["known_kt"] > 0)
    out["coverage"] = out["known_kt"] / out["total_kt"]
    return out.reset_index()


def detect_events(status: pd.DataFrame, min_stop_weeks: int = 3, min_run_weeks: int = 3,
                  latency_days: int = 5) -> pd.DataFrame:
    """Dated stop / restart / start-up events from the weekly status.

    A stop is a run of at least ``min_stop_weeks`` stopped weeks after a running week; it is
    *seen* when the third stopped week is known (week end + ``latency_days`` for the ERA5
    cloud data). A restart is the first running week after a stop, confirmed by
    ``min_run_weeks`` running weeks. A start-up is the first confirmed running spell of a site
    that was never visible before (new plant). ``est_start`` is the earliest day inside the
    first stopped (or running) 56-day window, i.e. the earliest possible start."""
    ev = []
    for sid, d in status.sort_values("week_end").groupby("site_id"):
        st = d["status"].to_numpy(); we = d["week_end"].to_numpy(); r = d["r"].to_numpy()
        vis = d["visible"].to_numpy()
        n = len(st)
        ever_visible = False
        state = None  # last confirmed state
        i = 0
        while i < n:
            if st[i] == "unknown":
                i += 1; continue
            j = i
            while j < n and st[j] == st[i]:
                j += 1
            run_len = j - i
            need = min_stop_weeks if st[i] == "stopped" else min_run_weeks
            if run_len >= need:
                seen = pd.Timestamp(we[i + need - 1]) + pd.Timedelta(days=latency_days)
                rec = {"site_id": sid, "first_week": pd.Timestamp(we[i]),
                       "est_start": pd.Timestamp(we[i]) - pd.Timedelta(days=WINDOW_DAYS - 1),
                       "seen_date": seen, "weeks": run_len,
                       "end_week": pd.Timestamp(we[j - 1]),
                       "min_r": float(np.nanmin(r[i:j])) if st[i] == "stopped" else float(np.nanmax(r[i:j]))}
                if st[i] == "stopped" and state == "running":
                    ev.append({**rec, "event": "stop"})
                elif st[i] == "running" and state == "stopped":
                    ev.append({**rec, "event": "restart"})
                elif st[i] == "running" and state is None and not ever_visible and i > 0 and not vis[:i].any():
                    ev.append({**rec, "event": "start-up"})
                state = st[i]
            ever_visible = ever_visible or vis[i:j].any()
            i = j
    cols = ["site_id", "event", "est_start", "first_week", "seen_date", "end_week", "weeks", "min_r"]
    return pd.DataFrame(ev, columns=cols)


def match_known(events: pd.DataFrame, known: pd.DataFrame, tol_days: int = 45) -> pd.DataFrame:
    """For each known event, the satellite event of the same site and direction that starts
    within ``tol_days`` of it (or inside it). Adds ``seen_date`` and ``lead_days`` (days between
    the known event start and the day the satellite showed it; negative = before)."""
    rows = []
    for k in known.itertuples(index=False):
        kind = {"down": ("stop",), "up": ("restart", "start-up")}.get(k.expected_effect, ())
        e = events[(events["site_id"] == k.site_id) & events["event"].isin(kind)]
        ks, ke = pd.Timestamp(k.start), pd.Timestamp(k.end)
        e = e[(e["first_week"] >= ks - pd.Timedelta(days=tol_days)) & (e["first_week"] <= ke + pd.Timedelta(days=tol_days))]
        if len(e):
            f = e.iloc[0]
            rows.append({**k._asdict(), "seen": True, "sat_event": f.event, "seen_date": f.seen_date,
                         "days_after_start": (f.seen_date - ks).days})
        else:
            rows.append({**k._asdict(), "seen": False, "sat_event": None, "seen_date": pd.NaT,
                         "days_after_start": np.nan})
    return pd.DataFrame(rows)
