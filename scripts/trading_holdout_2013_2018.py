"""Out-of-sample test on 2013-2018 of the calendar-spread rule frozen in data/trading/frozen_rule_holdout.json.

None of these years was used to build or choose anything in the trading chapter (the activity index
started in 2019). The holdout index is rebuilt with exactly the 2019-2026 rules:
  * VIIRS S-NPP detections (FIRMS archive VIIRS_SNPP_SP, from 2012-01-19; NOAA-20 did not exist yet)
    within 1.5 km of each furnace, per UTC day;
  * ERA5 total cloud cover at each site (CDS point time series) for the clear-day test;
  * activity_index.daily_flags / weekly_status (56-day window, seasonal reference of the 3 previous
    years, cut 0.6, reference < 0.3 = too dim = unknown) / capacity_index. Because the reference needs
    3 years, the first status is in 2015; sites with no heat in their reference years (not built yet)
    are 'unknown', so a site only enters the index once it has run for 3 years.
SHFE prices: official SHFE daily settlement files (closing price of every copper delivery month), first
trading day of each week, read through the project PC browser (data/trading/shfe_cu_contracts_2012_2019.csv).
The rule, costs and report follow the frozen file; nothing was tuned on these years."""
import os, sys, json, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "src"))
from orbitcu import activity_index as A, trading as T
from orbitcu.sites import load_sites
from orbitcu.geolocate import haversine_km
D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/"
RAW = os.environ.get("HOLDOUT_RAW", "/home/user/data_run/")
OUT = D + "trading/"; FIG = os.path.join(HERE, "..", "docs", "latex", "figures"); lines = []
def say(s): print(s); lines.append(s)
FROZEN = json.load(open(os.path.join(HERE, "..", "data", "trading", "frozen_rule_holdout.json")))

class Leg: id, name, country, lat, lon, capacity_kt = "PL-LEGNICA", "Legnica", "Poland", 51.18, 16.115, 160.0
sites = load_sites() + [Leg()]

# 1. day masks from the FIRMS archive (same 1.5 km rule as production.viirs_monthly)
mk = []
for s in sites:
    f = RAW + f"firms/raw/{s.id}.csv"
    if not os.path.exists(f): continue
    d = pd.read_csv(f)
    if len(d) == 0: continue
    d = d[haversine_km(d.latitude.values, d.longitude.values, s.lat, s.lon) <= 1.5]
    d["day"] = pd.to_datetime(d.acq_date); d["month"] = d.day.dt.strftime("%Y-%m")
    for m, g in d.groupby("month"):
        bits = 0
        for dd in g.day.dt.day.unique(): bits |= 1 << (int(dd) - 1)
        mk.append(dict(site_id=s.id, month=m, mask=f"{bits:08x}", n_det=len(g), frp_sum=round(g.frp.sum())))
masks = pd.DataFrame(mk); masks.to_csv(D + "production/snpp_site_masks_2012_2018.csv", index=False)
say(f"[data] VIIRS S-NPP 2012-2018: {len(masks)} site-months with detections, {masks.site_id.nunique()} sites")

# 2. holdout activity index, same rules as 2019-2026
daily = A.daily_flags(masks, sites, RAW + "era5ts/out", "2012-01-01", "2018-12-31", nodata_before="2012-01-19")
A.WINDOW_DAYS = 56
st = A.weekly_status(daily, reference="seasonal", min_ref=0.3)
# sites only from their start of operation (frozen rule): smelters built after the holdout are left out of
# the watched capacity instead of counting as unknown (public start dates; Southeast Copper first anode late 2018)
START = {"CD-KAMOA": "2025-12-29", "IN-KUTCH": "2024-03-28", "ID-MANYAR": "2024-06-01", "CN-CHIFENG-YJ": "2019-01-01", "CN-SOUTHEAST": "2018-10-01"}
st = st[~st.apply(lambda q: q.site_id in START and q.week_end < pd.Timestamp(START[q.site_id]), axis=1)]
idx = A.capacity_index(st, sites)
idx["total_kt"] = idx.week_end.map(lambda d: sum(s.capacity_kt or 0 for s in sites if s.id not in START or d >= pd.Timestamp(START[s.id])))
idx["coverage"] = idx.known_kt / idx.total_kt; idx = idx.set_index("week_end")
st.to_csv(D + "activity_index/weekly_status_2012_2018.csv", index=False); idx.to_csv(D + "activity_index/weekly_index_2012_2018.csv")
world = idx.running_share.where(idx.coverage >= 0.5)
cap = {s.id: (s.capacity_kt or 0) for s in sites}; ctry = {s.id: s.country for s in sites}
k = st[(st.status != "unknown") & st.site_id.map(lambda x: ctry[x] == "China")].assign(cap=lambda z: z.site_id.map(cap))
g = k.assign(run=(k.status == "running") * k.cap).groupby("week_end")[["run", "cap"]].sum()
china = (g.run / g.cap).where(g.cap >= 0.5 * sum(c for x, c in cap.items() if ctry[x] == "China" and x not in START))
say(f"[index] world share defined from {world.first_valid_index():%Y-%m-%d}, {world.notna().sum()} weeks, mean {world.mean():.2f}, mean coverage {idx.coverage[world.notna()].mean():.2f}; China share {china.notna().sum()} weeks")

# 3. tradable SHFE calendar-spread P&L (next month long, third month short, held Monday to Monday)
C = pd.read_csv(D + "trading/shfe_cu_contracts_2012_2019.csv", parse_dates=["week_monday"]).pivot(index="week_monday", columns="delivery", values="close_cny_t")
wk = C.index
def sym(d, kk): m = d.month - 1 + kk; return (d.year + m // 12) % 100 * 100 + m % 12 + 1
r = pd.Series([(C[sym(d, 1)].iloc[i + 1] / C[sym(d, 1)].iloc[i] - C[sym(d, 3)].iloc[i + 1] / C[sym(d, 3)].iloc[i]) if i + 1 < len(wk) else np.nan for i, d in enumerate(wk)], index=wk)
r = r.loc[:"2018-12-31"]

def surprise(x, L=52):
    med = x.shift(1).rolling(L, min_periods=int(.6 * L)).median(); dev = x - med
    return dev / dev.shift(1).rolling(52, min_periods=26).std()
def rule(sig, ret=r, hold=16, cost=4e-4, shift=0):
    z = surprise(sig)
    if shift: z = pd.Series(np.roll(z.values, shift), index=z.index)
    z = T.to_trade_dates(z, 1).reindex(ret.index)
    b = (-z).clip(-1, 1).rolling(hold, min_periods=1).mean()
    w = (b * 0.10 / T.past_vol(ret)).clip(-1, 1).fillna(0)
    return w * ret - w.diff().abs().fillna(0) * cost, w
def sh(x): x = x.dropna(); x = x[x.index >= x.ne(0).idxmax()] if x.ne(0).any() else x; return x.mean() / x.std() * np.sqrt(52) if len(x) > 20 and x.std() > 0 else np.nan

p, w = rule(world); live = p[p.index >= p.ne(0).idxmax()]
say(f"[holdout] frozen rule (world share, L 52, hold 16): first trade {live.index[0]:%Y-%m-%d}, {len(live)} weeks; Sharpe {sh(p):.2f}; return {live.mean()*52*100:.2f} %/yr at {live.std()*np.sqrt(52)*100:.2f} % vol")
say("[holdout] by year " + ", ".join(f"{y} {sh(live[live.index.year == y]):+.2f}" for y in sorted(set(live.index.year))))
say(f"[holdout] costs 8 / 20 bp: {sh(rule(world, cost=8e-4)[0]):.2f} / {sh(rule(world, cost=20e-4)[0]):.2f}")
rng = np.random.default_rng(13); n = world.notna().sum()
pl = np.array([sh(rule(world, shift=int(rng.integers(26, max(27, n - 26))))[0]) for _ in range(1000)])
say(f"[holdout] placebo (1,000 time shifts of the signal): share with Sharpe >= real {np.nanmean(pl >= sh(p)):.3f}; placebo median {np.nanmedian(pl):+.2f}")
pc, _ = rule(china); say(f"[holdout] secondary check, same rule with China share: Sharpe {sh(pc):.2f}")
carry = ((0.10 / T.past_vol(r)).clip(0, 1).fillna(0) * r).loc[live.index[0]:]
say(f"[holdout] always long the spread over the same weeks: Sharpe {sh(carry):.2f}; mean weight of the rule {w.loc[live.index[0]:].mean():+.2f}")
crit = "SUPPORTED" if sh(p) > 0 and np.nanmean(pl >= sh(p)) < 0.10 else "NOT SUPPORTED"
say(f"[holdout] criterion fixed in advance ({FROZEN['success_criterion']}): {crit}")
pd.DataFrame({"pnl": p, "weight": w, "world_share": world.reindex(p.index, method="ffill")}).to_csv(OUT + "holdout_2013_2018_pnl.csv")

fig, axs = plt.subplots(2, 1, figsize=(7.2, 4.2), sharex=True, gridspec_kw=dict(height_ratios=[1, 1.3]))
axs[0].plot(world, color="#1f5fa8", lw=1); axs[0].set_ylabel("world running share"); axs[0].spines[["top", "right"]].set_visible(False)
axs[1].plot((1 + live).cumprod(), color="#c0392b", lw=1.3, label=f"frozen rule, Sharpe {sh(p):.2f}")
axs[1].plot((1 + carry).cumprod(), color="#888888", lw=1, label=f"always long the spread, {sh(carry):.2f}")
axs[1].axhline(1, color="#888", lw=.6); axs[1].legend(frameon=False, fontsize=7); axs[1].set_ylabel("growth of 1"); axs[1].spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.savefig(os.path.join(FIG, "holdout_2013_2018.png"), dpi=160); plt.close()
open(OUT + "holdout_2013_2018_summary.txt", "w").write("\n".join(lines) + "\n")
