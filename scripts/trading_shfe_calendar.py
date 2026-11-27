"""Shanghai calendar spread (next month vs third month) against Chinese smelter activity.

When Chinese refined copper is scarce now, the nearby SHFE contract trades above later ones
(backwardation). Spread = P(month+1) / P(month+3) - 1, first trading day of each week, from Sina
Finance contract prices (data/trading/shfe_calendar_weekly.csv, 2019-03 to 2026-11).
Tests: correlations with the China running-share surprise (lead, same period), China stops/restarts,
and a strategy search where the instrument is the calendar spread itself (long next month, short third
month: weekly P&L = change in the spread), same signals/rules/placebo as the other searches."""
import os, numpy as np, pandas as pd
from scipy.stats import spearmanr, norm, skew, kurtosis
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ORBITCU_DATA", "/mnt/project-files/data/")
src = open(os.path.join(HERE, "trading_fabricator_search.py")).read()
src = src[:src.index("R,NETS=search")].replace("os.path.dirname(os.path.abspath(__file__))", repr(HERE))
exec(src)
OUT = D + "trading/"; lines = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); lines.append(s)
C = pd.read_csv(D + "trading/shfe_calendar_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
sp = C.spread_m1_m3
RET.clear(); RET["CalSpread"] = (sp.shift(-1) - sp).rename("ret")
INSTR[:] = ["CalSpread"]; VOL.clear(); VOL["CalSpread"] = T.past_vol(RET["CalSpread"])
cn_ids = {s for s, v in sites.items() if v.country == "China"}
evc = ev[ev.site_id.isin(cn_ids)]
for e in ["stop", "restart"]:
    d = evc[evc.event == e].seen_date
    wk = pd.Series(1, index=d + pd.to_timedelta((6 - d.dt.weekday) % 7, unit="D")).groupby(level=0).sum().reindex(weeks, fill_value=0).astype(float)
    for w in [4, 13]: S[f"sat_China_{e}s_{w}w"] = wk.rolling(w).sum().where(weeks >= "2019-10-01")
satS = {k: v for k, v in S.items() if k.startswith("sat_")}
z = T.to_trade_dates(transforms(S["sat_China_share"])["lvl26"], 1).reindex(sp.index)
for h in [1, 4, 8, 13]:
    x = pd.concat([z, sp.shift(-h) - sp], axis=1).dropna().iloc[::h]; r, p = spearmanr(x.iloc[:, 0], x.iloc[:, 1])
    say(f"[corr] China surprise vs SHFE spread change next {h} w: rho {r:+.2f} p {p:.3f} n {len(x)}")
x = pd.concat([z, sp], axis=1).dropna(); r, p = spearmanr(x.iloc[:, 0], x.iloc[:, 1]); say(f"[corr] China surprise vs spread level: rho {r:+.2f} p {p:.3f} n {len(x)} (overlapping weeks)")
share = S["sat_China_share"].copy(); share.index = share.index + pd.Timedelta(days=1)
for h in [4, 13]:
    x = pd.concat([share.reindex(sp.index).diff(h), sp.diff(h)], axis=1).dropna().iloc[::h]; r, p = spearmanr(x.iloc[:, 0], x.iloc[:, 1])
    say(f"[corr] same-period {h} w change: China share vs SHFE spread rho {r:+.2f} p {p:.3f} n {len(x)}")
for e in ["stop", "restart"]:
    out = []
    for dte in evc[evc.event == e].seen_date:
        i = sp.index.searchsorted(dte + pd.Timedelta(days=1))
        if i + 4 < len(sp): out.append(sp.iloc[i + 4] - sp.iloc[i])
    base = (sp.shift(-4) - sp).dropna()
    say(f"[events] China {e}s n {len(out)}: spread change next 4 w {np.mean(out)*1e4:+.0f} bp vs average {base.mean()*1e4:+.0f} bp, t {(np.mean(out)-base.mean())/(base.std()/np.sqrt(len(out))):+.2f}")
R, NETS = search(satS, "real"); R = R.dropna(subset=["sharpe_train", "sharpe_test"]); R.to_csv(OUT + "shfe_calendar_search.csv", index=False)
rho = spearmanr(R.sharpe_train, R.sharpe_test)[0]; top = R.nlargest(10, "sharpe_train")
say(f"[search] {len(R)} strategies: rank corr {rho:.2f}; median test {R.sharpe_test.median():.2f}; top10 train {top.sharpe_train.mean():.2f} -> test {top.sharpe_test.mean():.2f}; best all {R.sharpe_all.max():.2f}")
say(top[["signal", "transform", "smooth", "position", "direction", "sharpe_train", "sharpe_test"]].round(2).to_string(index=False))
rng = np.random.default_rng(4); pl = []
for _ in range(30):
    sh = int(rng.integers(52, 300))
    q, _ = search({k: pd.Series(np.roll(v.values, sh), index=v.index) for k, v in satS.items()}, "p"); q = q.dropna(subset=["sharpe_train", "sharpe_test"])
    pl.append((spearmanr(q.sharpe_train, q.sharpe_test)[0], q.nlargest(10, "sharpe_train").sharpe_test.mean(), q.sharpe_all.max()))
pl = np.array(pl); pd.DataFrame(pl, columns=["rank_corr", "top10_test", "best_all"]).to_csv(OUT + "shfe_calendar_placebo.csv", index=False)
say(f"[placebo] rank corr median {np.median(pl[:,0]):.2f} (>= real {np.mean(pl[:,0]>=rho):.2f}); top10 test median {np.median(pl[:,1]):.2f} (>= real {np.mean(pl[:,1]>=top.sharpe_test.mean()):.2f}); best all median {np.median(pl[:,2]):.2f} (>= real {np.mean(pl[:,2]>=R.sharpe_all.max()):.2f})")
best = R.loc[R.sharpe_all.idxmax()]; key = tuple(best[["signal", "transform", "smooth", "instrument", "position", "direction"]])
x = NETS[key].loc["2019":"2026"].dropna(); x = x[x.index >= x.ne(0).idxmax()]; n = len(x); srw = x.mean() / x.std()
N = len(R); emax = np.sqrt(np.var(R.sharpe_all / np.sqrt(52))) * ((1 - 0.5772) * norm.ppf(1 - 1 / N) + 0.5772 * norm.ppf(1 - 1 / (N * np.e)))
dsr = norm.cdf((srw - emax) * np.sqrt(n - 1) / np.sqrt(1 - skew(x) * srw + (kurtosis(x, fisher=False) - 1) / 4 * srw ** 2))
say(f"[dsr] best {key}: Sharpe {best.sharpe_all:.2f} (train {best.sharpe_train:.2f}, test {best.sharpe_test:.2f}); deflated Sharpe probability {dsr:.2f}")
open(OUT + "shfe_calendar_summary.txt", "w").write("\n".join(lines) + "\n")
