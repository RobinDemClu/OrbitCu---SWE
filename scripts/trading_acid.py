"""Chinese sulphuric acid price against Chinese smelter activity.

Copper smelters turn the sulphur of the concentrate into sulphuric acid (about 3 t of acid per t of
copper); in China smelter acid is a large share of supply, so fewer smelters running should mean less
acid and higher acid prices, an effect the copper price does not have to show.
Data: NBS 'market prices of important means of production', sulphuric acid 98 %, every 10 days
(early, middle, late month), Dec 2021 - Nov 2026 (data/trading/china_acid_nbs.csv, fetched from
stats.gov.cn through the project PC browser). Copper (electrolytic) from the same release as a control.
Tests: (1) same-period changes of China running share vs acid price; (2) China supply surprise vs the
acid change over the next 1, 3, 6 periods; (3) China stops / restarts vs the acid change after them;
(4) placebo by time shift for the best relation."""
import os, sys, numpy as np, pandas as pd
from scipy.stats import spearmanr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu import trading as T
from orbitcu.sites import load_sites
D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/"
A = pd.read_csv(D + "trading/china_acid_nbs.csv", parse_dates=["date"]).set_index("date").sort_index()
sites = {s.id: s for s in load_sites()}
st = pd.read_csv(D + "activity_index/weekly_status.csv", parse_dates=["week_end"])
st = st[st.site_id.map(lambda x: x in sites and sites[x].country == "China")]
st["cap"] = st.site_id.map(lambda x: sites[x].capacity_kt or 0)
k = st[st.status != "unknown"].assign(run=lambda z: (z.status == "running") * z.cap)
g = k.groupby("week_end")[["run", "cap"]].sum(); tot = sum(s.capacity_kt or 0 for s in sites.values() if s.country == "China")
cn = (g.run / g.cap).where(g.cap >= 0.5 * tot)
# China share as known at each acid date: last week ending at least 7 days before (ERA5 delay)
known = cn.copy(); known.index = known.index + pd.Timedelta(days=7)
X = pd.DataFrame({"acid": np.log(A.acid_cny_t), "cu": np.log(A.cu_cny_t),
                  "share": known.reindex(A.index, method="ffill"),
                  "share_now": cn.reindex(A.index, method="ffill")})
med = X.share.rolling(18, min_periods=9).median().shift(1)        # ~6 months of 10-day periods
X["surprise"] = X.share - med
lines = []
def say(s): print(s); lines.append(s)
for h in [1, 3, 6, 9]:
    d = X[["share_now", "acid", "cu"]].diff(h).iloc[::h].dropna()
    r, p = spearmanr(d.share_now, d.acid); rc, pc = spearmanr(d.share_now, d.cu)
    say(f"same period {h*10} d: China share vs acid rho {r:+.2f} (p {p:.3f}), vs copper {rc:+.2f} (p {pc:.3f}), n {len(d)}")
for h in [1, 3, 6]:
    f = (X.acid.shift(-h) - X.acid); d = pd.concat([X.surprise, f], axis=1).dropna().iloc[::h]
    r, p = spearmanr(d.iloc[:, 0], d.iloc[:, 1]); say(f"lead: surprise vs acid change next {h*10} d: rho {r:+.2f} (p {p:.3f}) n {len(d)}")
ev = pd.read_csv(D + "activity_index/events.csv", parse_dates=["seen_date"])
ev = ev[ev.site_id.map(lambda x: x in sites and sites[x].country == "China") & (ev.seen_date >= A.index.min())]
for e in ["stop", "restart"]:
    out = []
    for dte in ev[ev.event == e].seen_date:
        i = X.index.searchsorted(dte)
        if i + 3 < len(X): out.append(X.acid.iloc[i + 3] - X.acid.iloc[i])
    base = (X.acid.shift(-3) - X.acid).dropna()
    if out: say(f"after China {e}s (n {len(out)}): acid change next 30 d {np.mean(out)*100:+.1f} % vs average {base.mean()*100:+.1f} % (sd {base.std()*100:.1f} %)")
# placebo for the same-period 90-day relation
d0 = X[["share_now", "acid"]].diff(9).iloc[::9].dropna(); r0 = spearmanr(d0.share_now, d0.acid)[0]
rng = np.random.default_rng(1); pl = []
for _ in range(500):
    sh = pd.Series(np.roll(X.share_now.values, int(rng.integers(18, len(X) - 18))), index=X.index)
    dd = pd.concat([sh.diff(9), X.acid.diff(9)], axis=1).iloc[::9].dropna(); pl.append(spearmanr(dd.iloc[:, 0], dd.iloc[:, 1])[0])
say(f"placebo (shifted share) for the 90-day relation: share of |rho| >= {abs(r0):.2f}: {np.mean(np.abs(pl) >= abs(r0)):.2f}")
X.to_csv(D + "trading/china_acid_vs_share.csv"); open(D + "trading/acid_summary.txt", "w").write("\n".join(lines) + "\n")
# placebo for the lead relations: shift the surprise series in time
for h in [1, 3]:
    f = (X.acid.shift(-h) - X.acid)
    d = pd.concat([X.surprise, f], axis=1).dropna().iloc[::h]; r0 = spearmanr(d.iloc[:, 0], d.iloc[:, 1])[0]
    pl = []
    for _ in range(1000):
        s = pd.Series(np.roll(X.surprise.values, int(rng.integers(18, len(X) - 18))), index=X.index)
        dd = pd.concat([s, f], axis=1).dropna().iloc[::h]; pl.append(spearmanr(dd.iloc[:, 0], dd.iloc[:, 1])[0])
    say(f"placebo lead {h*10} d: real rho {r0:+.2f}; share of shifted surprises with rho <= real: {np.mean(np.array(pl) <= r0):.3f}")
# event t-statistics
for e in ["stop", "restart"]:
    out = [X.acid.iloc[X.index.searchsorted(dte) + 3] - X.acid.iloc[X.index.searchsorted(dte)] for dte in ev[ev.event == e].seen_date if X.index.searchsorted(dte) + 3 < len(X)]
    base = (X.acid.shift(-3) - X.acid).dropna()
    say(f"China {e}s: t = {(np.mean(out) - base.mean()) / (base.std() / np.sqrt(len(out))):+.2f}")
open(D + "trading/acid_summary.txt", "w").write("\n".join(lines) + "\n")
