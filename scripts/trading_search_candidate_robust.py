"""Robustness of the one family that survived the 2013-2026 search placebo: change in the running share of
the Americas smelters (Chuquicamata, Caletones, Ilo; Kamoa from 2025-12) -> Japanese cable makers
(Furukawa, Sumitomo Electric, Fujikura) against the Nikkei 225.

Reuses the data and signal code of trading_search_2013_2026.py (everything above its backtest section is
executed here unchanged). Checks: neighbourhood grid, the same signal on other instruments, one country at
a time and leave-one-site-out, drift (always long), costs, block bootstrap, 1,000 time-shift placebos,
exposure to copper and to the Japanese market, by year."""
import os, sys, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "trading_search_2013_2026.py")).read()
exec(compile(src.split("# ------------------------------------------------------------------ vectorised backtest")[0], "search_prefix", "exec"))
lines = []
def say(s): print(s, flush=True); lines.append(s)
JP = ["Fab_Furukawa", "Fab_SumitomoElec", "Fab_Fujikura"]
RET["JP_basket"] = pd.concat([RET[j] for j in JP], axis=1).mean(axis=1)
def share(countries, drop=()):
    kk = k[k.country.isin(countries) & ~k.site_id.isin(drop)]; ss = st[st.country.isin(countries) & ~st.site_id.isin(drop)]
    g = kk.groupby("week_end")[["run", "cap"]].sum(); tot = ss.groupby("week_end").cap.sum()
    return (g.run / g.cap).where(g.cap >= 0.5 * tot.reindex(g.index))
AM = S["AmericasAF_share"]
def zsig(x, c=8, sm=4, shift=0, lag=1):
    if shift: x = pd.Series(np.roll(x.values, shift), index=x.index)
    ch = x - x.shift(c); z = ch / ch.shift(1).rolling(52, min_periods=26).std()
    if sm > 1: z = z.rolling(sm, min_periods=1).mean()
    return T.to_trade_dates(z, lag).reindex(idxW)
def strat(z, inst="JP_basket", hold=8, pos="sign", cost=None, direction=1):
    r = RET[inst]; raw = np.sign(z) if pos == "sign" else (z / 2).clip(-1, 1)
    raw = raw.rolling(hold, min_periods=1).mean() if hold > 1 else raw
    w = (raw.fillna(0) * 0.10 / T.past_vol(r)).clip(-1, 1).fillna(0).where(r.notna(), 0)
    c = (4e-4 if inst not in ("COMEX", "LME", "SHFE") else 2e-4) if cost is None else cost
    return direction * w * r.fillna(0) - c * w.diff().abs().fillna(0), w
def sh(x): x = x[x.ne(0).cummax()]; return x.mean() / x.std() * np.sqrt(52) if len(x) > 52 and x.std() > 0 else np.nan
BASE = dict(c=8, sm=4, hold=8)
p0, w0 = strat(zsig(AM))
say(f"[candidate] AmericasAF share chg8, smooth 4, sign, hold 8, long the JP cable basket vs Nikkei when the share rises: Sharpe {sh(p0):.2f} (2013-19 {sh(p0[:'2019']):.2f}, 2020-26 {sh(p0['2020':]):.2f}); return {p0[p0.ne(0).cummax()].mean()*5200:.1f} %/yr")
say(f"[signal] Americas share defined on {AM.notna().mean():.0%} of weeks; distinct values {AM.round(3).nunique()}; share of weeks the 8-week change is non-zero {(AM - AM.shift(8)).ne(0)[AM.notna()].mean():.0%}")

# 1. neighbourhood
G = []
for c in [2, 4, 6, 8, 10, 13, 17, 26]:
    for sm in [1, 2, 4, 8]:
        for hold in [1, 4, 8, 16]:
            G.append(dict(c=c, sm=sm, hold=hold, sharpe=sh(strat(zsig(AM, c, sm), hold=hold)[0])))
G = pd.DataFrame(G)
say(f"[grid] 128 neighbours on the JP basket: share Sharpe > 0 {np.mean(G.sharpe > 0):.2f}, median {G.sharpe.median():.2f}, > 0.5 {np.mean(G.sharpe > 0.5):.2f}")
say("[grid] median Sharpe by change window: " + ", ".join(f"{c}w {v:.2f}" for c, v in G.groupby('c').sharpe.median().items()))
say("[grid] median Sharpe by hold: " + ", ".join(f"{c}w {v:.2f}" for c, v in G.groupby('hold').sharpe.median().items()))
# 2. other instruments, same rule
say("[instruments] same rule: " + ", ".join(f"{i} {sh(strat(zsig(AM), inst=i)[0]):+.2f}" for i in ["Fab_Furukawa", "Fab_SumitomoElec", "Fab_Fujikura", "Fab_Prysmian", "Fab_Nexans", "Fab_Mueller", "Fab_Hailiang", "COMEX", "LME", "SHFE", "CuAl"]))
# 3. which smelters
say("[sites] one country: " + ", ".join(f"{c} {sh(strat(zsig(share([c])))[0]):+.2f}" for c in ["Chile", "Peru"]))
say("[sites] leave one out: " + ", ".join(f"without {d} {sh(strat(zsig(share(['Chile', 'Peru', 'DR Congo'], [d])))[0]):+.2f}" for d in ["CL-CHUQUI", "CL-CALETONES", "PE-ILO"]))
say("[sites] other regions, same rule on JP basket: " + ", ".join(f"{r_} {sh(strat(zsig(S[r_ + '_share']))[0]):+.2f}" for r_ in ["China", "AsiaExCN", "EuropeRU"]) + f", world {sh(strat(zsig(S['world_share']))[0]):+.2f}")
# 4. drift and exposures
lo = (0.10 / T.past_vol(RET['JP_basket'])).clip(0, 1).fillna(0) * RET['JP_basket'].fillna(0)
say(f"[drift] always long the JP basket vs Nikkei: Sharpe {sh(lo.loc[p0.ne(0).idxmax():]):.2f}; mean weight of the rule {w0[p0.ne(0).cummax()].mean():+.2f}, share of weeks long {(w0 > 0)[p0.ne(0).cummax()].mean():.0%}")
X = pd.concat([p0, RET["COMEX"], lo, RET["JP_basket"].shift(1)], axis=1, keys=["p", "cu", "lo", "lag"]).dropna(); X = X[X.p.ne(0).cummax()]
b = np.linalg.lstsq(np.c_[np.ones(len(X)), X[["cu", "lo"]].values], X.p.values, rcond=None)[0]; e = X.p - np.c_[np.ones(len(X)), X[["cu", "lo"]].values] @ b
say(f"[exposure] P&L on copper and on the long-only basket: betas {b[1]:+.3f} / {b[2]:+.3f}; residual Sharpe {(b[0] + e).mean() / e.std() * np.sqrt(52):.2f}; corr with copper {X.p.corr(X.cu):+.2f}")
# 5. costs
say("[costs] per leg 4 (base) / 8 / 20 / 40 bp: " + " / ".join(f"{sh(strat(zsig(AM), cost=c)[0]):.2f}" for c in [4e-4, 8e-4, 20e-4, 40e-4]) + f"; turnover {w0.diff().abs().sum() / (len(w0) / 52):.1f} per year")
# 6. block bootstrap
x = p0[p0.ne(0).cummax()].values; rng = np.random.default_rng(5); B = []
for _ in range(5000):
    st0 = rng.integers(0, len(x) - 8, len(x) // 8 + 1); bb = np.concatenate([x[s:s + 8] for s in st0])[:len(x)]; B.append(bb.mean() / bb.std() * np.sqrt(52))
say(f"[bootstrap] 8-week blocks, 90 % interval of the Sharpe: {np.percentile(B, 5):.2f} to {np.percentile(B, 95):.2f}; P(Sharpe <= 0) {np.mean(np.array(B) <= 0):.3f}")
# 7. single-rule placebo (no search correction: the search placebo above already corrects for the selection)
n = AM.notna().sum(); PLr = np.array([sh(strat(zsig(AM, shift=int(rng.integers(52, n - 52))))[0]) for _ in range(1000)])
say(f"[placebo] 1,000 time shifts of the Americas signal, this rule only: share >= real {np.nanmean(PLr >= sh(p0)):.3f}, median {np.nanmedian(PLr):+.2f}")
# 8. Japanese copper stocks never used in any search (Yahoo adjusted closes, first trading day of each week)
JPN = pd.read_csv(R + "japan_copper_stocks_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
n225 = wret(JPN["^N225"])
NEW = {"SWCC (cables) 5805.T": "5805.T", "Mitsubishi Materials 5711.T": "5711.T", "Sumitomo Metal Mining 5713.T": "5713.T",
       "Mitsui Kinzoku 5706.T": "5706.T", "Dowa 5714.T": "5714.T"}
for nm, t in NEW.items(): RET[t] = (wret(JPN[t]) - n225).reindex(idxW).clip(-0.5, 0.5)
RET["JP_new_smelters"] = pd.concat([RET[t] for t in ["5711.T", "5713.T", "5706.T", "5714.T"]], axis=1).mean(axis=1)
say("[new stocks] same frozen rule, untouched Japanese stocks vs Nikkei: " + ", ".join(f"{nm} {sh(strat(zsig(AM), inst=t)[0]):+.2f}" for nm, t in NEW.items())
    + f"; smelter basket {sh(strat(zsig(AM), inst='JP_new_smelters')[0]):+.2f}")
for t in ["5805.T", "JP_new_smelters"]:
    pp = strat(zsig(AM), inst=t)[0]; PLn = np.array([sh(strat(zsig(AM, shift=int(rng.integers(52, n - 52))), inst=t)[0]) for _ in range(500)])
    say(f"[new stocks] {t}: 2013-19 {sh(pp[:'2019']):+.2f}, 2020-26 {sh(pp['2020':]):+.2f}; placebo share >= real {np.nanmean(PLn >= sh(pp)):.3f}")
# 9. by year
yy = p0[p0.ne(0).cummax()]
def shy(x): return x.mean() / x.std() * np.sqrt(52) if x.std() > 0 else np.nan
say("[by year] " + ", ".join(f"{y} {shy(yy[yy.index.year == y]):+.2f}" for y in sorted(set(yy.index.year))))
on = w0.ne(0)[yy.index]; ep = (on != on.shift()).cumsum()[on]; E = yy[on].groupby(ep).sum()
say(f"[episodes] {len(E)} separate positions (8-week holds after a change in the Americas share); {np.sum(E > 0)} gained; mean {E.mean()*100:+.2f} % per position")
say(f"[by year] positive years {sum(yy[yy.index.year == y].mean() > 0 for y in set(yy.index.year))} of {len(set(yy.index.year))}")
pd.DataFrame({"pnl": p0, "weight": w0, "americas_share": AM.reindex(idxW, method='ffill')}).to_csv(OUT + "search_candidate_pnl.csv")
G.to_csv(OUT + "search_candidate_grid.csv", index=False)
fig, axs = plt.subplots(1, 2, figsize=(7.4, 2.8), gridspec_kw=dict(width_ratios=[1.4, 1]))
axs[0].plot((1 + yy).cumprod(), color="#1f5fa8", lw=1.3, label=f"rule, Sharpe {sh(p0):.2f}")
axs[0].plot((1 + lo.loc[yy.index]).cumprod(), color="#888", lw=1, label=f"always long, {sh(lo.loc[yy.index]):.2f}")
axs[0].axhline(1, color="#888", lw=.6); axs[0].legend(frameon=False, fontsize=7); axs[0].set_ylabel("growth of 1"); axs[0].spines[["top", "right"]].set_visible(False)
axs[1].hist(PLr[~np.isnan(PLr)], bins=40, color="#bbb"); axs[1].axvline(sh(p0), color="#c0392b"); axs[1].set_xlabel("Sharpe of time-shifted signals", fontsize=7.5)
axs[1].set_title(f"this rule only: {np.nanmean(PLr >= sh(p0)):.1%} of 1,000 placebos as good", fontsize=8); axs[1].spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.savefig(os.path.join(FIG, "search_candidate.png"), dpi=160); plt.close()
# 10. calendar check: shifts by whole years keep the time of year of every signal change but break the link
# to the actual year; if the gain came from a seasonal pattern in Japanese stocks, these would score as high
yrs_sh = [52 * j for j in range(1, 13)] + [-52 * j for j in range(1, 13)]
PS = {j: sh(strat(zsig(AM, shift=j))[0]) for j in yrs_sh}
say(f"[calendar] signal moved by whole years (24 shifts): median Sharpe {np.nanmedian(list(PS.values())):+.2f}, max {np.nanmax(list(PS.values())):+.2f}, real {sh(p0):.2f}")
mo = pd.Series(np.sign(w0[yy.index]).values, index=yy.index.month)
say("[calendar] share of position weeks by month: " + ", ".join(f"{m} {v:.0%}" for m, v in (mo.ne(0).groupby(level=0).sum() / mo.ne(0).sum()).items()))
say("[delay] trading 1 (base) / 2 / 3 / 4 / 6 weeks after the satellite week: " + " / ".join(f"{sh(strat(zsig(AM, lag=L))[0]):.2f}" for L in [1, 2, 3, 4, 6]))
open(OUT + "search_candidate_summary.txt", "w").write("\n".join(lines) + "\n")
