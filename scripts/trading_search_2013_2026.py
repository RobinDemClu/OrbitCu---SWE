"""Large strategy search on 2013-2026 with the homogeneous S-NPP activity index, and honest controls.

Signals (known at each Sunday week end, traded 8 days later = 1-week delay for ERA5):
  running share (world, China, Asia ex-China, Europe+Russia, Americas+Africa), number of stopped smelters,
  stops / restarts / net events over 4 and 13 weeks (world) and stops / restarts over 13 weeks (China).
Transforms: level vs trailing median (13 / 26 / 52 weeks, scaled by the past 52-week std of the
  deviation) or change over 4 / 8 / 13 weeks (same scaling); smoothing 1 or 4 weeks.
Positions: sign or clip(z/2); both directions; holding 1 or 8 weeks (mean of the last raw positions);
  10 % volatility target on the past 26-week volatility, |weight| <= 1; costs 2 bp per turnover for one
  leg, 4 bp for two legs.
Instruments (weekly, Monday to Monday): COMEX copper, LME copper 3M, LME copper against aluminium,
  zinc and their mean, SHFE copper (third-month contract held), SHFE against LME, SHFE calendar spread
  (next month long, third month short, same contracts held), 7 copper fabricators against their home
  index and their basket.
Controls, all on the same search run with time-shifted placebo signals (200 shifts of 52-400 weeks):
  1. best full-period Sharpe, share of strategies above 0 and mean of the top 1 %;
  2. strategies positive in both halves (2013-2019, 2020-2026);
  3. walk-forward selection, the real test: each January from 2016 the 10 best strategies on all data
     before that date are chosen and traded for the year; the 2016-2026 result is out of sample;
  4. deflated Sharpe ratio of the best strategy (Bailey and Lopez de Prado 2014)."""
import os, sys, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
from scipy.stats import norm, skew, kurtosis
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "src"))
from orbitcu import trading as T
from orbitcu.sites import load_sites
D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/"
R = os.path.join(HERE, "..", "data", "trading") + "/"
OUT = D + "trading/"; FIG = os.path.join(HERE, "..", "docs", "latex", "figures"); lines = []
def say(s): print(s, flush=True); lines.append(s)

# ------------------------------------------------------------------ prices, 2013-2026, weekly Mondays
P1 = pd.read_csv(R + "prices_2012_2018_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
def chain(early, late):
    """Join two price series: early before the first late date, late after (levels agree on overlap)."""
    late = late.dropna(); return pd.concat([early[early.index < late.index[0]], late]).sort_index()
lme = chain(P1.lme_cu_3m, T.load_weekly_prices(R + "lme_cu_3m_weekly.csv", "lme_3m_usd_t"))
hg = chain(P1.hg, T.load_weekly_prices(R + "comex_hg_weekly.csv", "hg_usd_lb"))
AZ = pd.read_csv(R + "lme_al_zn_3m_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
al, zn = chain(P1.lme_al_3m, AZ.lme_al_3m_usd_t), chain(P1.lme_zn_3m, AZ.lme_zn_3m_usd_t)
# SHFE contracts: official SHFE files 2012-2019, Sina 2019-2026
c1 = pd.read_csv(R + "shfe_cu_contracts_2012_2019.csv", parse_dates=["week_monday"])
c2 = pd.read_csv(R + "shfe_cu_contracts_weekly.csv", parse_dates=["week_monday"]); c2["delivery"] = c2.contract.str[2:].astype(int)
C = pd.concat([c2[["week_monday", "delivery", "close_cny_t"]], c1]).drop_duplicates(["week_monday", "delivery"]).pivot(index="week_monday", columns="delivery", values="close_cny_t").sort_index()
def sym(d, k): m = d.month - 1 + k; return (d.year + m // 12) % 100 * 100 + m % 12 + 1
def hold_ret(k):
    out = []
    for i, d in enumerate(C.index[:-1]):
        s = sym(d, k); out.append(C[s].iloc[i + 1] / C[s].iloc[i] - 1 if s in C else np.nan)
    return pd.Series(out + [np.nan], index=C.index)
shfe3 = hold_ret(3); cal = hold_ret(1) - hold_ret(3)
W = pd.date_range("2012-01-02", "2026-11-23", freq="W-MON")
def wret(p): return T.weekly_returns(p.reindex(W, method="ffill", limit=1))
RET = {"COMEX": wret(hg), "LME": wret(lme), "CuAl": wret(lme) - wret(al), "CuZn": wret(lme) - wret(zn),
       "CuBase": wret(lme) - (wret(al) + wret(zn)) / 2, "SHFE": shfe3.reindex(W), "SHFEvsLME": shfe3.reindex(W) - wret(lme),
       "CalSpread": cal.reindex(W)}
FB = pd.read_csv(R + "fabricators_weekly_log.csv", parse_dates=["week_monday"]).set_index("week_monday")
TK = {"Prysmian": ("PRY.MI", "FTSEMIB.MI"), "Nexans": ("NEX.PA", "^FCHI"), "Mueller": ("MLI", "^GSPC"), "Furukawa": ("5801.T", "^N225"),
      "SumitomoElec": ("5802.T", "^N225"), "Fujikura": ("5803.T", "^N225"), "Hailiang": ("002203.SZ", "399001.SZ")}
fab = []
for f, (s, ix) in TK.items():
    rs = chain(P1[s], np.exp(FB[f + "_stock"]) * P1[s].loc["2018-01-01"]); ri = chain(P1[ix], np.exp(FB[f + "_index"]) * P1[ix].loc["2018-01-01"])
    RET["Fab_" + f] = wret(rs) - wret(ri); fab.append(RET["Fab_" + f])
RET["Fab_basket"] = pd.concat(fab, axis=1).mean(axis=1)
TWO = {k for k in RET if k not in ("COMEX", "LME", "SHFE")}
for k in RET: RET[k] = RET[k].reindex(W).loc["2013-01-01":].clip(-0.5, 0.5)
idxW = RET["COMEX"].index
say("[data] instruments: " + ", ".join(f"{k} {v.first_valid_index():%Y-%m}" for k, v in RET.items()))

# ------------------------------------------------------------------ satellite signals
A = D + "activity_index/snpp_2012_2026/"
sites = {s.id: s for s in load_sites()}; sites["PL-LEGNICA"] = type("L", (), dict(country="Poland", capacity_kt=160.0))
st = pd.read_csv(A + "weekly_status.csv", parse_dates=["week_end"])
ix = pd.read_csv(A + "weekly_index.csv", parse_dates=["week_end"]).set_index("week_end")
ev = pd.read_csv(A + "events.csv", parse_dates=["seen_date"])
st["cap"] = st.site_id.map(lambda x: sites[x].capacity_kt or 0); st["country"] = st.site_id.map(lambda x: sites[x].country)
REG = {"China": ["China"], "AsiaExCN": ["Japan", "South Korea", "India", "Indonesia", "Iran"], "EuropeRU": ["Poland", "Germany", "Bulgaria", "Russia"], "AmericasAF": ["Chile", "Peru", "DR Congo"]}
k = st[st.status != "unknown"].assign(run=lambda z: (z.status == "running") * z.cap)
S = {"world_share": ix.running_share.where(ix.coverage >= 0.5), "n_stopped": ix.n_stopped.astype(float).where(ix.coverage >= 0.5)}
for r_, cs in REG.items():
    g = k[k.country.isin(cs)].groupby("week_end")[["run", "cap"]].sum(); tot = st[st.country.isin(cs)].groupby("week_end").cap.sum()
    S[f"{r_}_share"] = (g.run / g.cap).where(g.cap >= 0.5 * tot.reindex(g.index))
weeks = ix.index
ev = ev[ev.event.isin(["stop", "restart"]) & (ev.seen_date >= "2013-06-01")]
def evcount(e, w, cn=None):
    q = ev[ev.event == e]
    if cn: q = q[q.site_id.map(lambda x: sites[x].country == cn)]
    d = q.seen_date; wk = pd.Series(1, index=d + pd.to_timedelta((6 - d.dt.weekday) % 7, unit="D")).groupby(level=0).sum()
    return wk.reindex(weeks, fill_value=0).astype(float).rolling(w).sum().where(weeks >= "2013-09-01")
for e in ["stop", "restart"]:
    for w in [4, 13]: S[f"{e}s_{w}w"] = evcount(e, w)
    S[f"China_{e}s_13w"] = evcount(e, 13, "China")
S["net_events_13w"] = S["restarts_13w"] - S["stops_13w"]
SIGN = list(S)
def transforms(x):
    out = {}
    for L in [13, 26, 52]:
        dev = x - x.shift(1).rolling(L, min_periods=int(.6 * L)).median(); out[f"lvl{L}"] = dev / dev.shift(1).rolling(52, min_periods=26).std()
    for c in [4, 8, 13]:
        ch = x - x.shift(c); out[f"chg{c}"] = ch / ch.shift(1).rolling(52, min_periods=26).std()
    return out
def zmatrix(shift=0):
    cols = {}
    for n in SIGN:
        x = S[n]
        if shift: x = pd.Series(np.roll(x.values, shift), index=x.index)
        for tn, z in transforms(x).items():
            for sm in [1, 4]:
                zz = z.rolling(sm, min_periods=1).mean() if sm > 1 else z
                cols[(n, tn, sm)] = T.to_trade_dates(zz, 1).reindex(idxW)
    return pd.DataFrame(cols)

# ------------------------------------------------------------------ vectorised backtest
VOL = {k: T.past_vol(v) for k, v in RET.items()}
RV = {k: v.fillna(0).values for k, v in RET.items()}; VALID = {k: v.notna().values for k, v in RET.items()}
yrs = idxW.year.values
def run_all(Z):
    """Weekly net P&L of every strategy: dict key -> np.array (T,)."""
    Zv = Z.values; out = {}; names = list(Z.columns)
    for pos in ["sign", "scaled"]:
        raw0 = np.sign(Zv) if pos == "sign" else np.clip(Zv / 2, -1, 1)
        raw0 = np.where(np.isnan(Zv), np.nan, raw0)
        for hold in [1, 8]:
            raw = pd.DataFrame(raw0).rolling(hold, min_periods=1).mean().values if hold > 1 else raw0
            for inst in RET:
                w = np.clip(np.nan_to_num(raw) * (0.10 / VOL[inst].values)[:, None], -1, 1)
                w = np.nan_to_num(w); w[~VALID[inst]] = 0
                cost = (4e-4 if inst in TWO else 2e-4) * np.abs(np.diff(w, axis=0, prepend=0))
                g = w * RV[inst][:, None]
                for dr in [1, -1]:
                    pnl = dr * g - cost
                    out[(inst, pos, hold, dr)] = (pnl, names)
    return out
def sharpe_mat(pnl, mask):
    x = pnl[mask]; live = np.cumsum(np.abs(x), axis=0) > 0
    x = np.where(live, x, np.nan); n = live.sum(0)
    m, s = np.nanmean(x, 0), np.nanstd(x, 0)
    return np.where((n > 52) & (s > 0), m / s * np.sqrt(52), np.nan)
M_ALL = idxW >= "2013-01-01"; M_H1 = idxW < "2020-01-01"; M_H2 = idxW >= "2020-01-01"
def summarize(res):
    rows = []
    for key, (pnl, names) in res.items():
        a, h1, h2 = sharpe_mat(pnl, M_ALL), sharpe_mat(pnl, M_H1), sharpe_mat(pnl, M_H2)
        for j, nm in enumerate(names): rows.append((*nm, *key, a[j], h1[j], h2[j]))
    return pd.DataFrame(rows, columns=["signal", "transform", "smooth", "instrument", "position", "hold", "direction", "sharpe_all", "sharpe_h1", "sharpe_h2"])
def walk_forward(res, top=10, first=2016):
    """Each January, pick the top strategies by Sharpe on all weeks before it; trade them for the year."""
    keys = [(key, j) for key, (pnl, names) in res.items() for j in range(pnl.shape[1])]
    big = np.concatenate([pnl for pnl, _ in res.values()], axis=1)
    oos = np.zeros(len(idxW)); picks = {}
    for y in range(first, 2027):
        tr = (yrs < y) & M_ALL; te = yrs == y
        sr = sharpe_mat(big, tr); sr = np.where(np.isnan(sr), -9, sr)
        best = np.argsort(-sr)[:top]; picks[y] = [keys[b] for b in best]
        oos[te] = big[te][:, best].mean(1)
    o = pd.Series(oos, index=idxW)[idxW.year >= first]
    return o, picks

say("[search] building real signals")
Z0 = zmatrix(); res = summarize(run_all(Z0)); res = res.dropna(subset=["sharpe_all"])
res.to_csv(OUT + "search_2013_2026.csv", index=False)
N = len(res); best = res.loc[res.sharpe_all.idxmax()]
both = np.mean((res.sharpe_h1 > 0) & (res.sharpe_h2 > 0)); top1 = res.sharpe_all.nlargest(max(1, N // 100)).mean()
say(f"[search] {N} strategies on 2013-2026; share Sharpe > 0: {np.mean(res.sharpe_all > 0):.2f}; positive in both halves: {both:.2f}; top 1 % mean {top1:.2f}; best {best.sharpe_all:.2f}")
say("[search] best 10:\n" + res.nlargest(10, "sharpe_all").round(2).to_string(index=False))
say("[search] best by instrument: " + ", ".join(f"{k} {v:.2f}" for k, v in res.groupby("instrument").sharpe_all.max().sort_values(ascending=False).items()))
wf, picks = walk_forward(run_all(Z0))
def shs(x): x = x[x.ne(0).cummax()]; return x.mean() / x.std() * np.sqrt(52)
say(f"[walk-forward] top 10 chosen each January on past data only, traded 2016-2026: Sharpe {shs(wf):.2f}; by year " + ", ".join(f"{y} {shs(wf[wf.index.year == y]):+.2f}" for y in range(2016, 2027)))
say("[walk-forward] instruments of the 10 picks by year: " + "; ".join(f"{y}: " + ",".join(sorted({q[0][0] for q in p})) for y, p in picks.items()))

# ------------------------------------------------------------------ placebo: the whole search on time-shifted signals
rng = np.random.default_rng(21)
NPL = int(os.environ.get("N_PLACEBO", 200))
SHIFTS = [int(rng.integers(52, 400)) for _ in range(NPL)]
def one_placebo(sh):
    rr = run_all(zmatrix(sh)); q = summarize(rr).dropna(subset=["sharpe_all"]); w_, _ = walk_forward(rr)
    return dict(shift=sh, best=q.sharpe_all.max(), share_pos=np.mean(q.sharpe_all > 0), both=np.mean((q.sharpe_h1 > 0) & (q.sharpe_h2 > 0)),
                top1=q.sharpe_all.nlargest(max(1, len(q) // 100)).mean(), wf=shs(w_))
from multiprocessing import Pool
if os.environ.get("REUSE_PLACEBO") and os.path.exists(OUT + "search_2013_2026_placebo.csv"):
    PL = pd.read_csv(OUT + "search_2013_2026_placebo.csv")   # same seed and shifts, saved by a previous full run
else:
    with Pool(4) as pool:
        PL = []
        for i, r_ in enumerate(pool.imap_unordered(one_placebo, SHIFTS)):
            PL.append(r_)
            if i % 10 == 9: print(f"placebo {i+1}/{NPL}", flush=True)
    PL = pd.DataFrame(PL); PL.to_csv(OUT + "search_2013_2026_placebo.csv", index=False)
real = dict(best=best.sharpe_all, share_pos=np.mean(res.sharpe_all > 0), both=both, top1=top1, wf=shs(wf))
for kk, v in real.items():
    say(f"[placebo] {kk}: real {v:+.3f}, placebo median {PL[kk].median():+.3f}, share of placebos >= real {np.mean(PL[kk] >= v):.3f}")
# deflated Sharpe of the best
rr0 = run_all(Z0); key = (best.instrument, best.position, best.hold, best.direction)
pnl, names = rr0[key]; j = names.index((best['signal'], best['transform'], best['smooth'])); x = pd.Series(pnl[:, j], index=idxW)
x = x[x.ne(0).cummax()]; n = len(x); srw = x.mean() / x.std()
emax = np.sqrt(np.var(res.sharpe_all / np.sqrt(52))) * ((1 - 0.5772) * norm.ppf(1 - 1 / N) + 0.5772 * norm.ppf(1 - 1 / (N * np.e)))
dsr = norm.cdf((srw - emax) * np.sqrt(n - 1) / np.sqrt(1 - skew(x) * srw + (kurtosis(x, fisher=False) - 1) / 4 * srw ** 2))
say(f"[dsr] best strategy deflated Sharpe probability {dsr:.2f} (needs > 0.95)")

fig, axs = plt.subplots(1, 3, figsize=(7.4, 2.6))
for ax, (kk, lab) in zip(axs, [("best", "best Sharpe 2013-2026"), ("share_pos", "share of strategies with Sharpe > 0"), ("wf", "walk-forward Sharpe 2016-2026")]):
    ax.hist(PL[kk], bins=15, color="#888888", alpha=.7); ax.axvline(real[kk], color="#c0392b", lw=2)
    ax.set_xlabel(lab, fontsize=7.5); ax.set_title(f"{np.mean(PL[kk] >= real[kk]):.0%} of placebos as good", fontsize=8); ax.spines[["top", "right"]].set_visible(False)
axs[0].set_ylabel("placebo searches"); plt.tight_layout(); plt.savefig(os.path.join(FIG, "search_2013_2026.png"), dpi=160); plt.close()
open(OUT + "search_2013_2026_summary.txt", "w").write("\n".join(lines) + "\n")
