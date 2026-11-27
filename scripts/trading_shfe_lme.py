"""Shanghai against London: do Chinese smelter stops move the SHFE-LME copper spread?

Idea: when Chinese smelters run below normal, domestic refined copper gets scarce, Shanghai prices rise
against London and the import window opens. The satellite sees those smelters before the market data
(SMM surveys, customs) are out.
Data: SHFE copper main contract daily close (Sina Finance CU0, CNY/t incl. VAT), USD/CNY (Yahoo CNY=X),
LME 3-month (Westmetall), first trading day of each week (data/trading/shfe_lme_weekly.csv).
Import ratio = SHFE / (LME 3M x USDCNY x 1.13); log changes are the spread moves.
Instruments: 'SHFEvsLME' = SHFE return minus LME return (currency hedged); 'Ratio' = change in the
import ratio (includes the currency).
Signals: all satellite signals of trading_strategy_search.py plus China-only stops / restarts over 4 and
13 weeks. Same transforms, rules (1-week delay, 10 % vol target, 2 bp), training 2019-2022, test
2023-2026, time-shift placebo and deflated Sharpe."""
import os, numpy as np, pandas as pd
from scipy.stats import spearmanr, norm, skew, kurtosis
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ORBITCU_DATA", "/mnt/project-files/data/")
src = open(os.path.join(HERE, "trading_fabricator_search.py")).read()
src = src[:src.index("R,NETS=search")].replace("os.path.dirname(os.path.abspath(__file__))", repr(HERE))
exec(src)
OUT = D + "trading/"
lines = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); lines.append(s)

W = pd.read_csv(D + "trading/shfe_lme_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
rs, rl = T.weekly_returns(W.shfe_cny_t), T.weekly_returns(W.lme_3m_usd_t.astype(float))
lr = np.log(W.import_ratio)
RET.clear(); RET.update({"SHFEvsLME": (rs - rl).rename("ret"), "Ratio": (np.exp(lr.shift(-1) - lr) - 1).rename("ret")})
INSTR[:] = list(RET); VOL.clear(); VOL.update({k: T.past_vol(v) for k, v in RET.items()})

# China-only events
cn_ids = {s for s, v in sites.items() if v.country == "China"}
evc = ev[ev.site_id.isin(cn_ids)]
for e in ["stop", "restart"]:
    d = evc[evc.event == e].seen_date
    wk = pd.Series(1, index=d + pd.to_timedelta((6 - d.dt.weekday) % 7, unit="D")).groupby(level=0).sum().reindex(weeks, fill_value=0).astype(float)
    for w in [4, 13]: S[f"sat_China_{e}s_{w}w"] = wk.rolling(w).sum().where(weeks >= "2019-10-01")
satS = {k: v for k, v in S.items() if k.startswith("sat_")}

# 1. plain correlations: China surprise (known at week end + 1 week) vs later spread moves
z = T.to_trade_dates(transforms(S["sat_China_share"])["lvl26"], 1)
for h in [1, 4, 8, 13]:
    fwd = (lr.shift(-h) - lr)
    x = pd.concat([z.reindex(lr.index), fwd], axis=1).dropna().iloc[::h]
    r, p = spearmanr(x.iloc[:, 0], x.iloc[:, 1]); say(f"[corr] China share surprise vs import-ratio change next {h} w: rho {r:.2f} p {p:.3f} n {len(x)}")
for h in [4, 13]:
    x = pd.concat([S["sat_China_share"].diff(h).rename("a"), lr.reindex(S["sat_China_share"].index, method="nearest").diff(h).rename("b")], axis=1).dropna().iloc[::h]
    r, p = spearmanr(x.a, x.b); say(f"[corr] same-period {h} w change: China share vs import ratio rho {r:.2f} p {p:.3f} n {len(x)}")

# 2. strategy search
R, NETS = search(satS, "real"); R = R.dropna(subset=["sharpe_train", "sharpe_test"])
R.to_csv(OUT + "shfe_lme_search.csv", index=False)
rho = spearmanr(R.sharpe_train, R.sharpe_test)[0]; top = R.nlargest(10, "sharpe_train")
say(f"[search] {len(R)} strategies: rank corr {rho:.2f}; median test {R.sharpe_test.median():.2f}; share test>0 {np.mean(R.sharpe_test>0):.2f}; top10 test {top.sharpe_test.mean():.2f}; best all {R.sharpe_all.max():.2f}")
say(top[["signal", "transform", "smooth", "instrument", "position", "direction", "sharpe_train", "sharpe_test"]].round(2).to_string(index=False))
cn = R[R.signal.str.contains("China")]
say(f"[search] China signals only ({len(cn)}): median test {cn.sharpe_test.median():.2f}; top10 train->test {cn.nlargest(10,'sharpe_train').sharpe_test.mean():.2f}")
rng = np.random.default_rng(9); pl = []
for _ in range(30):
    sh = int(rng.integers(52, 300))
    q, _ = search({k: pd.Series(np.roll(v.values, sh), index=v.index) for k, v in satS.items()}, "p"); q = q.dropna(subset=["sharpe_train", "sharpe_test"])
    pl.append((spearmanr(q.sharpe_train, q.sharpe_test)[0], q.nlargest(10, "sharpe_train").sharpe_test.mean(), q.sharpe_all.max()))
pl = np.array(pl); pd.DataFrame(pl, columns=["rank_corr", "top10_test", "best_all"]).to_csv(OUT + "shfe_lme_placebo.csv", index=False)
say(f"[placebo] rank corr median {np.median(pl[:,0]):.2f} (share >= real {np.mean(pl[:,0]>=rho):.2f}); top10 test median {np.median(pl[:,1]):.2f} (share >= real {np.mean(pl[:,1]>=top.sharpe_test.mean()):.2f}); best all median {np.median(pl[:,2]):.2f} (share >= real {np.mean(pl[:,2]>=R.sharpe_all.max()):.2f})")
best = R.loc[R.sharpe_all.idxmax()]; key = tuple(best[["signal", "transform", "smooth", "instrument", "position", "direction"]])
x = NETS[key].loc["2019":"2026"].dropna(); x = x[x.index >= x.ne(0).idxmax()]; n = len(x); srw = x.mean() / x.std()
N = len(R); emax = np.sqrt(np.var(R.sharpe_all / np.sqrt(52))) * ((1 - 0.5772) * norm.ppf(1 - 1 / N) + 0.5772 * norm.ppf(1 - 1 / (N * np.e)))
dsr = norm.cdf((srw - emax) * np.sqrt(n - 1) / np.sqrt(1 - skew(x) * srw + (kurtosis(x, fisher=False) - 1) / 4 * srw ** 2))
say(f"[dsr] best full-period strategy {key}: Sharpe {best.sharpe_all:.2f}, train {best.sharpe_train:.2f}, test {best.sharpe_test:.2f}; deflated Sharpe probability {dsr:.2f}")
open(OUT + "shfe_lme_summary.txt", "w").write("\n".join(lines) + "\n")
