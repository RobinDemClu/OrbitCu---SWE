"""Homogeneous weekly activity index 2012-2026 from one satellite (VIIRS S-NPP), for long backtests.

The 2019-2026 index used S-NPP and NOAA-20 together; NOAA-20 did not exist before 2018, so a series that
starts in 2012 must use S-NPP alone to keep the same sensitivity over the whole period. Inputs:
  * S-NPP day masks: production/snpp_site_masks_2012_2018.csv (FIRMS yearly archives) and
    production/snpp_site_masks.csv (2018-2026); months present in both are OR-ed;
  * clear days: ERA5 point series 2012-2018 (HOLDOUT_RAW/era5ts/out) and the clear flags already stored
    in activity_index/daily_flags.parquet for 2018-2026 (ERA5, same rule).
Same rules as activity_index (56-day window, seasonal reference, cut 0.6, min reference 0.3, carry 4
weeks). Smelters are counted only from their start of operation."""
import os, sys, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "src"))
from orbitcu import activity_index as A
from orbitcu.sites import load_sites
D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/"
RAW = os.environ.get("HOLDOUT_RAW", "/home/user/data_run/")
class Leg: id, name, country, lat, lon, capacity_kt = "PL-LEGNICA", "Legnica", "Poland", 51.18, 16.115, 160.0
sites = load_sites() + [Leg()]
START = {"CD-KAMOA": "2025-12-29", "IN-KUTCH": "2024-03-28", "ID-MANYAR": "2024-06-01", "CN-CHIFENG-YJ": "2019-01-01", "CN-SOUTHEAST": "2018-10-01"}

masks = pd.concat([pd.read_csv(D + "production/snpp_site_masks_2012_2018.csv", dtype={"mask": str}),
                   pd.read_csv(D + "production/snpp_site_masks.csv", dtype={"mask": str})])
early = A.daily_flags(masks, sites, RAW + "era5ts/out", "2012-01-01", "2018-06-30", nodata_before="2012-01-19")
late_clear = pd.read_parquet(D + "activity_index/daily_flags.parquet")[["site_id", "day", "clear", "has_cloud"]]
late_clear = late_clear[late_clear.day >= "2018-07-01"]
# hot days 2018-07 on from the S-NPP masks only
hot = []
for r in masks.itertuples(index=False):
    p, bits = pd.Period(r.month, "M"), int(str(r.mask), 16)
    if p.start_time < pd.Timestamp("2018-07-01"): continue
    hot += [(r.site_id, p.start_time + pd.Timedelta(days=d)) for d in range(p.days_in_month) if bits >> d & 1]
hot = pd.DataFrame(hot, columns=["site_id", "day"]).drop_duplicates().assign(hot=True)
late = late_clear.merge(hot, on=["site_id", "day"], how="left").fillna({"hot": False})
late["day"] = pd.to_datetime(late.day)
daily = pd.concat([early, late[["site_id", "day", "hot", "clear", "has_cloud"]]], ignore_index=True).sort_values(["site_id", "day"])
A.WINDOW_DAYS = 56
st = A.weekly_status(daily, reference="seasonal", min_ref=0.3)
st = st[~st.apply(lambda q: q.site_id in START and q.week_end < pd.Timestamp(START[q.site_id]), axis=1)]
idx = A.capacity_index(st, sites)
idx["total_kt"] = idx.week_end.map(lambda d: sum(s.capacity_kt or 0 for s in sites if s.id not in START or d >= pd.Timestamp(START[s.id])))
idx["coverage"] = idx.known_kt / idx.total_kt
ev = A.detect_events(st)
os.makedirs(D + "activity_index/snpp_2012_2026", exist_ok=True)
st.to_csv(D + "activity_index/snpp_2012_2026/weekly_status.csv", index=False)
idx.to_csv(D + "activity_index/snpp_2012_2026/weekly_index.csv", index=False)
ev.to_csv(D + "activity_index/snpp_2012_2026/events.csv", index=False)
print(idx.set_index("week_end").resample("YE")[["coverage", "running_share", "n_stopped"]].mean().round(2).to_string())
print(ev.groupby([ev.seen_date.astype(str).str[:4], "event"]).size().unstack().fillna(0).astype(int).to_string())
# agreement with the two-satellite index 2019-2026
old = pd.read_csv(D + "activity_index/weekly_index.csv", parse_dates=["week_end"]).set_index("week_end").running_share
new = idx.set_index("week_end").running_share
j = pd.concat([old, new], axis=1, keys=["two", "snpp"]).dropna()
print(f"2019-2026 running share, two satellites vs S-NPP only: corr {j.two.corr(j.snpp):.2f}, n {len(j)}")
