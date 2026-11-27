"""Add the Chinese smelters of the ICSG 2026 top-20 list (and Yunnan Copper's Southwest Copper)
that were not watched yet, and recompute their weekly running status.

Inputs (paths by environment variable, defaults are the project data folder):
  FIRMS_RAW   : folder(s) of raw VIIRS detections (parquet), separated by ':'
  TCC_DIR     : ERA5 hourly cloud cover per site (``<site>_*.parquet``)
Outputs (data/activity_index/china_extra/):
  extra_site_masks.csv  : day masks (same format as the production masks)
  extra_daily_flags.parquet, extra_weekly_status.csv
  weekly_status_with_extra.csv : the existing weekly status plus the new sites
"""
import glob, os, sys
import numpy as np, pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from orbitcu import activity_index as A
from orbitcu.geolocate import haversine_km  # noqa: F401  (same distance as elsewhere)

D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/"
OUT = os.path.join(ROOT, "data", "activity_index", "china_extra")
REG = pd.read_csv(os.path.join(ROOT, "data", "registry", "china_extra_sites.csv"))
RADIUS = 1.5


class Site:
    def __init__(self, r):
        self.id, self.lat, self.lon, self.capacity_kt, self.country = r.id, r.lat, r.lon, r.capacity_kt_cu, "China"


sites = [Site(r) for r in REG.itertuples()]


def masks_from_raw(paths, sites):
    rows = []
    for sensor in ["SNPP", "NOAA20"]:
        fs = [f for p in paths for f in glob.glob(os.path.join(p, "**", "*.parquet"), recursive=True) if sensor in f]
        parts = [pd.read_parquet(f) for f in fs]  # months with no detection are empty files
        d = pd.concat([x[["latitude", "longitude", "acq_date", "frp"]] for x in parts if "latitude" in x], ignore_index=True)
        d = d.drop_duplicates()
        for s in sites:
            near = d[haversine_km(d.latitude.values, d.longitude.values, s.lat, s.lon) <= RADIUS].copy()
            near["day"] = pd.to_datetime(near.acq_date)
            near["month"] = near.day.dt.to_period("M")
            for m, g in near.groupby("month"):
                bits = 0
                for dd in g.day.dt.day.unique():
                    bits |= 1 << (int(dd) - 1)
                rows.append(dict(site_id=s.id, month=str(m), mask=format(bits, "x"), n_det=len(g),
                                 frp_sum=g.frp.sum(), sensor=sensor))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    raw = os.environ.get("FIRMS_RAW", "").split(":")
    masks = masks_from_raw([p for p in raw if p], sites)
    masks.to_csv(os.path.join(OUT, "extra_site_masks.csv"), index=False)
    daily = A.daily_flags(masks, sites, os.environ["TCC_DIR"], "2018-01-01", "2026-10-31")
    daily.to_parquet(os.path.join(OUT, "extra_daily_flags.parquet"))
    st = A.weekly_status(daily, reference="seasonal", min_ref=0.3)
    st.to_csv(os.path.join(OUT, "extra_weekly_status.csv"), index=False)
    old = pd.read_csv(D + "activity_index/weekly_status.csv", parse_dates=["week_end"])
    allst = pd.concat([old, st], ignore_index=True)
    allst.to_csv(os.path.join(OUT, "weekly_status_with_extra.csv"), index=False)
    # summary per new site
    s = st.copy(); s["year"] = s.week_end.dt.year
    summ = s.groupby("site_id").agg(visible_share=("visible", "mean"),
                                    running_weeks=("status", lambda x: (x == "running").sum()),
                                    stopped_weeks=("status", lambda x: (x == "stopped").sum()),
                                    unknown_weeks=("status", lambda x: (x == "unknown").sum()))
    act = daily.assign(month=daily.day.dt.to_period("Y")).groupby(["site_id", "month"]).apply(
        lambda g: (g.hot & g.clear).sum() / max(g.clear.sum(), 1)).unstack().round(2)
    summ.to_csv(os.path.join(OUT, "extra_summary.csv"))
    act.to_csv(os.path.join(OUT, "extra_yearly_activity.csv"))
    print(summ.to_string()); print(act.to_string())
