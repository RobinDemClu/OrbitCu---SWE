"""China-local markets, one rule fixed in advance.

Earlier tests found that after a Chinese shortfall (running share below its 26-week median, as seen by
the satellites) three China-local prices move the expected way one to two months later: the Shanghai/
London import ratio, the Shanghai calendar spread and the Chinese sulphuric acid price. To avoid another
search, a single rule is written down before running it:
  signal  z = China running-share surprise (level vs trailing 26-week median, in standard deviations),
          known one week after the week it describes (ERA5 delay);
  target  raw = clip(-z / 2, -1, 1): long tightness when China runs below normal;
  holding each weekly signal is held 8 weeks (overlapping book = mean of the last 8 raw signals);
  size    10 % volatility target on the instrument's past 26-week volatility, |weight| <= 1;
  costs   4 bp per unit of turnover (two legs);
  markets (a) SHFE calendar spread, long next month / short third month (P&L = change in spread);
          (b) long SHFE / short LME, currency hedged; (c) equal mix of (a) and (b);
          (d) SHFE copper outright, as a check that the effect is local and not on the price level.
Reported: Sharpe 2019-2026 and the two halves, placebo p-value (1,000 time shifts of the signal), the
1-week-holding version, and the correlation-by-horizon profile with placebo bands (figure)."""
import os, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
from scipy.stats import spearmanr
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ORBITCU_DATA", "/mnt/project-files/data/")
src = open(os.path.join(HERE, "trading_fabricator_search.py")).read()
src = src[:src.index("R,NETS=search")].replace("os.path.dirname(os.path.abspath(__file__))", repr(HERE))
exec(src)   # S (signals), transforms(), T, D
OUT = D + "trading/"; FIG = os.path.join(HERE, "..", "docs", "latex", "figures"); lines = []
def say(s): print(s); lines.append(s)
cal = pd.read_csv(D + "trading/shfe_calendar_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday").spread_m1_m3
W = pd.read_csv(D + "trading/shfe_lme_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
acid = pd.read_csv(D + "trading/china_acid_nbs.csv", parse_dates=["date"]).set_index("date").acid_cny_t
idx = cal.index
ratio = np.log(W.import_ratio).reindex(idx)
acid_w = np.log(acid).reindex(idx.union(acid.index)).ffill().reindex(idx)
rs, rl = T.weekly_returns(W.shfe_cny_t).reindex(idx), T.weekly_returns(W.lme_3m_usd_t.astype(float)).reindex(idx)
z = T.to_trade_dates(transforms(S["sat_China_share"])["lvl26"], 1).reindex(idx)

# 1. correlation by horizon with placebo bands
rng = np.random.default_rng(7); H = range(1, 14); prof = []
series = {"Shanghai calendar spread": cal, "Shanghai/London ratio": ratio, "Chinese sulphuric acid": acid_w}
shifts = [int(rng.integers(52, 300)) for _ in range(300)]
for name, y in series.items():
    for h in H:
        f = y.shift(-h) - y
        def rho(sig):
            d = pd.concat([sig, f], axis=1).dropna().iloc[::h]; return spearmanr(d.iloc[:, 0], d.iloc[:, 1])[0] if len(d) > 10 else np.nan
        r0 = rho(z); pl = np.array([rho(pd.Series(np.roll(z.values, s), index=idx)) for s in shifts])
        prof.append(dict(market=name, h_weeks=h, rho=r0, p5=np.nanpercentile(pl, 5), p95=np.nanpercentile(pl, 95), share_le=np.nanmean(pl <= r0)))
P = pd.DataFrame(prof); P.to_csv(OUT + "china_local_horizon.csv", index=False)
fig, axs = plt.subplots(1, 3, figsize=(7.4, 2.6), sharey=True)
for ax, (name, q) in zip(axs, P.groupby("market", sort=False)):
    ax.fill_between(q.h_weeks, q.p5, q.p95, color="#cccccc", label="90 % of placebos")
    ax.plot(q.h_weeks, q.rho, "o-", color="#c0392b", ms=3, label="real signal"); ax.axhline(0, color="#888", lw=.7)
    ax.set_title(name, fontsize=8); ax.set_xlabel("weeks ahead"); ax.spines[["top", "right"]].set_visible(False)
axs[0].set_ylabel("rank corr. with later change"); axs[0].legend(frameon=False, fontsize=6.5)
plt.tight_layout(); plt.savefig(os.path.join(FIG, "china_local_horizon.png"), dpi=160); plt.close()
for name, q in P.groupby("market", sort=False):
    b = q.loc[q.rho.idxmin()]; say(f"[horizon] {name}: most negative rho {b.rho:+.2f} at {int(b.h_weeks)} w (placebo share <= {b.share_le:.2f}); rho at 8 w {q[q.h_weeks==8].rho.iloc[0]:+.2f}")

# 2. the fixed rule
def book(sig, hold):
    raw = (-sig / 2).clip(-1, 1)
    return raw.rolling(hold, min_periods=1).mean() if hold > 1 else raw
def run(sig, r, hold=8, cost=4e-4):
    w = (book(sig, hold) * 0.10 / T.past_vol(r)).clip(-1, 1).fillna(0)
    return w * r - w.diff().abs().fillna(0) * cost
def sh(x):
    x = x.dropna(); x = x[x.index >= x.ne(0).idxmax()] if x.ne(0).any() else x
    return x.mean() / x.std() * np.sqrt(52) if len(x) > 20 and x.std() > 0 else np.nan
R = {"calendar spread": (cal.shift(-1) - cal), "SHFE vs LME": rs - rl, "SHFE outright": rs}
def all_pnl(sig, hold=8):
    p = {k: run(sig, r, hold) for k, r in R.items()}
    a, b = p["calendar spread"], p["SHFE vs LME"]
    p["mix (spread + SHFE vs LME)"] = (a / a.std() + b / b.std()) / 2 * a.std()
    return p
rows = []
real = all_pnl(z)
for k, x in real.items():
    full = sh(x.loc["2019":"2026"])
    pl = []
    for s in shifts[:300] + [int(rng.integers(52, 300)) for _ in range(700)]:
        pl.append(sh(all_pnl(pd.Series(np.roll(z.values, s), index=idx))[k].loc["2019":"2026"]))
    rows.append(dict(market=k, sharpe_2019_2026=full, sharpe_2019_2022=sh(x.loc["2019":"2022"]), sharpe_2023_2026=sh(x.loc["2023":"2026"]),
                     placebo_p=np.nanmean(np.array(pl) >= full), sharpe_1w_hold=sh(all_pnl(z, 1)[k].loc["2019":"2026"])))
Rt = pd.DataFrame(rows); Rt.to_csv(OUT + "china_local_rule.csv", index=False); say(Rt.round(2).to_string(index=False))
eq = pd.DataFrame({k: (1 + v.loc["2019":"2026"].fillna(0)).cumprod() for k, v in real.items()})
fig, ax = plt.subplots(figsize=(7.2, 3.0))
for k, c in zip(eq, ["#1f5fa8", "#6a9fd8", "#888888", "#c0392b"]): ax.plot(eq.index, eq[k], color=c, lw=1.4 if "mix" in k else 1, label=k)
ax.axhline(1, color="#888", lw=.6); ax.legend(frameon=False, fontsize=7); ax.set_ylabel("growth of 1"); ax.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.savefig(os.path.join(FIG, "china_local_equity.png"), dpi=160); plt.close()
open(OUT + "china_local_summary.txt", "w").write("\n".join(lines) + "\n")
