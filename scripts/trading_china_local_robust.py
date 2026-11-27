"""Robustness of the China-local calendar-spread rule (scripts/trading_china_local.py).

The fixed rule (China running-share surprise, 26-week reference, clip(-z/2), 8-week holding, 10 % vol
target) gave Sharpe 0.41 on the SHFE calendar spread. Here it is stressed and its variants are explored:
  1. Grid of 1,440 variants: reference window L (13, 20, 26, 39, 52 weeks), holding (1-16 weeks), scale
     of the position (clip(-z/s), s = 1, 2, 4), signal delay (1 or 2 weeks), signal (China share, world
     share, China minus rest of world), sizing (vol target or fixed weight).
  2. Multiple-testing controls on the whole grid: the best Sharpe of the grid and the share of variants
     above zero against 200 time-shifted placebo signals; picking the best variant on 2019-2022 and
     reading its 2023-2026 result, against the same procedure on placebos.
  0. Tradability: the spread series is constant-maturity (next month vs third month), so it switches
     contracts at each month end and its weekly change is not a P&L. All tests below use the tradable
     P&L of the same two contracts held from Monday to Monday (data/trading/shfe_cu_contracts_weekly.csv,
     96 SHFE contracts from Sina Finance); the earlier result on the constant-maturity series is shown.
  3. Baselines: always long the spread (carry); the spread's own
     mean reversion; regression of the rule's P&L on those baselines (alpha).
  4. Block bootstrap (8-week blocks) for the Sharpe interval; leave-one-year-out; yearly results.
  5. Improvements that do not add free choices: averaging the positions over the plateau of reference
     windows and holdings (ensemble), and adding the other two China-local markets as a basket."""
import os, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ORBITCU_DATA", "/mnt/project-files/data/")
src = open(os.path.join(HERE, "trading_fabricator_search.py")).read()
src = src[:src.index("R,NETS=search")].replace("os.path.dirname(os.path.abspath(__file__))", repr(HERE))
exec(src)   # S (signals), T, D
OUT = D + "trading/"; FIG = os.path.join(HERE, "..", "docs", "latex", "figures"); lines = []
def say(s): print(s); lines.append(s)

cal = pd.read_csv(D + "trading/shfe_calendar_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday").spread_m1_m3
idx = cal.index; r_cm = (cal.shift(-1) - cal)          # constant-maturity spread change: switches contracts at month end
# tradable P&L: long the next-month contract and short the third-month contract chosen at Monday m, both held to Monday m+1
C = pd.read_csv(D + "trading/shfe_cu_contracts_weekly.csv", parse_dates=["week_monday"]).pivot(index="week_monday", columns="contract", values="close_cny_t").reindex(idx)
def sym(d, k): m = d.month - 1 + k; return f"CU{(d.year + m // 12) % 100:02d}{m % 12 + 1:02d}"
r = pd.Series([(C[sym(d, 1)].iloc[i + 1] / C[sym(d, 1)].iloc[i] - C[sym(d, 3)].iloc[i + 1] / C[sym(d, 3)].iloc[i]) if i + 1 < len(idx) else np.nan
               for i, d in enumerate(idx)], index=idx)
roll = pd.Series(idx.month != pd.Series(idx).shift(-1).dt.month.values, index=idx)
W = pd.read_csv(D + "trading/shfe_lme_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
r_ratio = (T.weekly_returns(W.shfe_cny_t) - T.weekly_returns(W.lme_3m_usd_t.astype(float))).reindex(idx)
A = pd.read_csv(D + "trading/china_acid_nbs.csv", parse_dates=["date"]).set_index("date").acid_cny_t
acid_w = np.log(A).reindex(idx.union(A.index)).ffill().reindex(idx); r_acid = acid_w.shift(-1) - acid_w   # not tradable, shown only
cs = S["sat_China_share"]; ws = S["sat_world_share"]
SIG = {"China": cs, "World": ws, "China-World": cs - ws}

def surprise(x, L):
    med = x.shift(1).rolling(L, min_periods=int(.6 * L)).median(); dev = x - med
    return dev / dev.shift(1).rolling(52, min_periods=26).std()
def zmat(shift=0):
    cols = {}
    for sn, x in SIG.items():
        for L in [13, 20, 26, 39, 52]:
            z = surprise(x, L)
            if shift: z = pd.Series(np.roll(z.values, shift), index=z.index)
            for lag in [1, 2]: cols[(sn, L, lag)] = T.to_trade_dates(z, lag).reindex(idx)
    return pd.DataFrame(cols)
HOLDS, SCALES = [1, 2, 4, 6, 8, 10, 12, 16], [1, 2, 4]
VOLR = T.past_vol(r); MASK = (idx >= pd.Timestamp("2019-01-01"))
def sharpe_cols(P, sl=slice(None)):
    X = P[sl]; X = X.where(X.ne(0).cummax())          # start each column at its first trade
    return X.mean() / X.std() * np.sqrt(52)
def grid(Z, ret=r, cost=4e-4):
    out = {}
    for s in SCALES:
        raw = (-Z / s).clip(-1, 1)
        for h in HOLDS:
            b = raw.rolling(h, min_periods=1).mean() if h > 1 else raw
            for sizing in ["vol", "fixed"]:
                w = (b.mul(0.10 / VOLR, axis=0) if sizing == "vol" else b).clip(-1, 1).fillna(0)
                pnl = w.mul(ret, axis=0) - w.diff().abs().fillna(0) * cost
                for c in Z.columns: out[c + (h, s, sizing)] = pnl[c]
    return pd.DataFrame(out)
names = ["signal", "L", "lag", "hold", "scale", "sizing"]
def summary(P):
    return pd.DataFrame({"all": sharpe_cols(P.loc["2019":"2026"]), "train": sharpe_cols(P.loc["2019":"2022"]), "test": sharpe_cols(P.loc["2023":"2026"])})

# ---------------------------------------------------------------- 0. tradable vs constant maturity
say(f"[data] tradable weekly P&L vs constant-maturity change: corr {r.corr(r_cm):.2f}; mean {r.mean()*1e4:+.1f} vs {r_cm.mean()*1e4:+.1f} bp; sd {r.std()*1e4:.1f} vs {r_cm.std()*1e4:.1f} bp; month-end weeks corr {r[roll].corr(r_cm[roll]):.2f}")
# ---------------------------------------------------------------- 1. grid
P = grid(zmat()); G = summary(P); G.index.names = names; G = G.reset_index()
G.to_csv(OUT + "china_local_grid.csv", index=False)
base = G[(G.signal == "China") & (G.L == 26) & (G.lag == 1) & (G.hold == 8) & (G.scale == 2) & (G.sizing == "vol")].iloc[0]
say(f"[grid] {len(G)} variants; fixed rule Sharpe {base['all']:.2f} (train {base.train:.2f}, test {base.test:.2f}); rank among variants {int((G['all'] > base['all']).sum()) + 1}")
say(f"[grid] share of variants with full-period Sharpe > 0: {np.mean(G['all'] > 0):.2f}; median {G['all'].median():.2f}; best {G['all'].max():.2f}")
for k in names:
    say(f"[grid] median Sharpe by {k}: " + ", ".join(f"{a} {b:+.2f}" for a, b in G.groupby(k)["all"].median().items()))
cn = G[(G.signal == "China") & (G.lag == 1) & (G.scale == 2) & (G.sizing == "vol")].pivot(index="L", columns="hold", values="all")
say("[grid] China, lag 1, scale 2, vol target: Sharpe by reference window (rows) and holding (columns)\n" + cn.round(2).to_string())
both = G[(G.train > 0) & (G.test > 0)]; say(f"[grid] variants positive in both halves: {len(both)} of {len(G)} ({len(both)/len(G):.0%})")

# ---------------------------------------------------------------- 2. multiple testing on the whole grid
rng = np.random.default_rng(11); PL = []
for k in range(200):
    sh = int(rng.integers(52, 300)); g = summary(grid(zmat(sh)))
    pick = g.train.idxmax()
    PL.append(dict(shift=sh, best_all=g["all"].max(), share_pos=np.mean(g["all"] > 0), median_all=g["all"].median(),
                   china_plateau=g.loc[[c for c in g.index if c[0] == "China" and c[2] == 1 and c[3] >= 6 and c[1] in (20, 26, 39) and c[4] == 2 and c[5] == "vol"], "all"].mean(),
                   picked_test=g.loc[pick, "test"]))
PL = pd.DataFrame(PL); PL.to_csv(OUT + "china_local_grid_placebo.csv", index=False)
Gi = G.set_index(names); pick = Gi.train.idxmax()
plateau = Gi.loc[[c for c in Gi.index if c[0] == "China" and c[2] == 1 and c[3] >= 6 and c[1] in (20, 26, 39) and c[4] == 2 and c[5] == "vol"], "all"].mean()
real = dict(best_all=G["all"].max(), share_pos=np.mean(G["all"] > 0), median_all=G["all"].median(), china_plateau=plateau, picked_test=Gi.loc[pick, "test"])
for k, v in real.items():
    say(f"[placebo grid] {k}: real {v:+.2f}, placebo median {PL[k].median():+.2f}, share of placebos >= real {np.mean(PL[k] >= v):.3f}")
say(f"[select] best on 2019-2022: {pick}, train {Gi.loc[pick,'train']:.2f} -> test {Gi.loc[pick,'test']:.2f}")

# ---------------------------------------------------------------- 3. tradability and baselines
z26 = zmat()[("China", 26, 1)]
def rule(z, ret=r, hold=8, s=2, cost=4e-4, skip=None):
    b = (-z / s).clip(-1, 1).rolling(hold, min_periods=1).mean()
    w = (b * 0.10 / T.past_vol(ret)).clip(-1, 1).fillna(0)
    if skip is not None: w = w.where(~skip, 0)
    return w * ret - w.diff().abs().fillna(0) * cost
def sh(x): x = x.dropna(); x = x[x.index >= x.ne(0).idxmax()]; return x.mean() / x.std() * np.sqrt(52)
p0 = rule(z26).loc["2019":"2026"]
say(f"[trade] fixed rule on the tradable P&L {sh(p0):.2f} (2019-22 {sh(p0.loc[:'2022']):.2f}, 2023-26 {sh(p0.loc['2023':]):.2f}); on the constant-maturity series {sh(rule(z26, ret=r_cm).loc['2019':'2026']):.2f}")
g_cm = rule(z26, ret=r_cm, cost=0).loc["2019":"2026"]; g_tr = rule(z26, cost=0).loc["2019":"2026"]; rm = roll.reindex(g_cm.index)
say(f"[trade] gross P&L per year, month-end weeks / other weeks: constant maturity {g_cm[rm].sum()/7.5*1e4:+.0f} / {g_cm[~rm].sum()/7.5*1e4:+.0f} bp, tradable {g_tr[rm].sum()/7.5*1e4:+.0f} / {g_tr[~rm].sum()/7.5*1e4:+.0f} bp")
carry = ((0.10 / T.past_vol(r)).clip(0, 1).fillna(0) * r).loc["2019":"2026"]
mr_sig = -(cal - cal.rolling(26, min_periods=13).mean()) / cal.rolling(26, min_periods=13).std()     # spread's own mean reversion, known at m
mr = rule(-mr_sig.shift(0)).loc["2019":"2026"]  # rule() uses -z: pass -signal so the position = +mr_sig
say(f"[baseline] always long spread {sh(carry):.2f}; spread mean reversion (26 w) {sh(mr):.2f}; corr of rule P&L with them {p0.corr(carry):+.2f} / {p0.corr(mr):+.2f}")
Xb = pd.concat([p0, carry, mr], axis=1).dropna(); Xb.columns = ["rule", "carry", "mr"]
Xm = np.c_[np.ones(len(Xb)), Xb[["carry", "mr"]].values]; bcoef, res, *_ = np.linalg.lstsq(Xm, Xb.rule.values, rcond=None)
e = Xb.rule.values - Xm @ bcoef; se = np.sqrt(np.diag(np.linalg.inv(Xm.T @ Xm)) * e.var(ddof=3))
say(f"[alpha] rule on baselines: alpha {bcoef[0]*52*1e4:+.1f} bp/yr (t {bcoef[0]/se[0]:+.2f}), beta carry {bcoef[1]:+.2f}, beta mean-rev {bcoef[2]:+.2f}")
for c in [2e-4, 4e-4, 8e-4, 12e-4, 20e-4]: say(f"[cost] {c*1e4:.0f} bp per turnover: Sharpe {sh(rule(z26, cost=c).loc['2019':'2026']):.2f}")
w8 = ((-z26 / 2).clip(-1, 1).rolling(8, min_periods=1).mean() * 0.10 / T.past_vol(r)).clip(-1, 1).fillna(0)
say(f"[trade] turnover {w8.diff().abs().loc['2019':'2026'].sum()/8:.1f} per year; mean |weight| {w8.abs().loc['2019':'2026'].mean():.2f}; share of weeks at the cap {np.mean(w8.abs().loc['2019':'2026'] >= .999):.2f}")

# ---------------------------------------------------------------- 4. bootstrap, leave-one-year-out
x = p0.dropna().values; n = len(x); B = []
for _ in range(5000):
    st = rng.integers(0, n - 8, size=n // 8 + 1); y = np.concatenate([x[s:s + 8] for s in st])[:n]; B.append(y.mean() / y.std() * np.sqrt(52))
say(f"[bootstrap] Sharpe {sh(p0):.2f}, 90 % interval {np.percentile(B,5):.2f} to {np.percentile(B,95):.2f}; P(Sharpe <= 0) {np.mean(np.array(B) <= 0):.2f}")
yrs = sorted(set(p0.index.year)); say("[by year] " + ", ".join(f"{y} {sh(p0[p0.index.year == y]):+.2f}" for y in yrs))
say("[leave one year out] " + ", ".join(f"-{y} {sh(p0[p0.index.year != y]):+.2f}" for y in yrs))

# ---------------------------------------------------------------- 5. improvements without new free choices
Z = zmat()
ens_cols = [("China", L, 1) for L in (13, 20, 26, 39, 52)]
def ens_rule(cols, holds=(4, 6, 8, 10, 12, 16), ret=r):
    b = sum((-Z[c] / 2).clip(-1, 1).rolling(h, min_periods=1).mean() for c in cols for h in holds) / (len(cols) * len(holds))
    w = (b * 0.10 / T.past_vol(ret)).clip(-1, 1).fillna(0)
    return w * ret - w.diff().abs().fillna(0) * 4e-4
E = {"fixed rule (L 26, hold 8)": p0,
     "ensemble all L, holds 4-16": ens_rule(ens_cols).loc["2019":"2026"],
     "ensemble L 20-39, holds 6-12": ens_rule([("China", L, 1) for L in (20, 26, 39)], (6, 8, 10, 12)).loc["2019":"2026"],
     "fixed rule on SHFE vs LME": rule(z26, ret=r_ratio).loc["2019":"2026"]}
E["basket spread + SHFE vs LME"] = (E["fixed rule (L 26, hold 8)"] / E["fixed rule (L 26, hold 8)"].std() + E["fixed rule on SHFE vs LME"] / E["fixed rule on SHFE vs LME"].std()) / 2 * E["fixed rule (L 26, hold 8)"].std()
Er = []
for k, v in E.items():
    Er.append(dict(variant=k, sharpe_2019_2026=sh(v), sharpe_2019_2022=sh(v.loc[:"2022"]), sharpe_2023_2026=sh(v.loc["2023":]), sharpe_without_2026_09_on=sh(v.loc[:"2026-08"])))
Er = pd.DataFrame(Er); say(Er.round(2).to_string(index=False)); Er.to_csv(OUT + "china_local_variants.csv", index=False)
# placebo for the ensemble
pe = []
for k in range(300):
    shf = int(rng.integers(52, 300)); Zs = zmat(shf)
    b = sum((-Zs[c] / 2).clip(-1, 1).rolling(h, min_periods=1).mean() for c in ens_cols for h in (4, 6, 8, 10, 12, 16)) / 30
    w = (b * 0.10 / T.past_vol(r)).clip(-1, 1).fillna(0); pe.append(sh((w * r - w.diff().abs().fillna(0) * 4e-4).loc["2019":"2026"]))
say(f"[placebo ensemble] real {sh(E['ensemble all L, holds 4-16']):.2f}; share of placebos >= real {np.mean(np.array(pe) >= sh(E['ensemble all L, holds 4-16'])):.3f}")

# ---------------------------------------------------------------- figures
fig, axs = plt.subplots(1, 2, figsize=(7.4, 2.9), gridspec_kw=dict(width_ratios=[1.15, 1]))
im = axs[0].imshow(cn.values, cmap="RdBu", vmin=-0.8, vmax=0.8, aspect="auto")
axs[0].set_xticks(range(len(cn.columns))); axs[0].set_xticklabels(cn.columns, fontsize=7); axs[0].set_yticks(range(len(cn.index))); axs[0].set_yticklabels(cn.index, fontsize=7)
for i in range(cn.shape[0]):
    for j in range(cn.shape[1]): axs[0].text(j, i, f"{cn.values[i, j]:.2f}", ha="center", va="center", fontsize=6)
axs[0].set_xlabel("holding, weeks"); axs[0].set_ylabel("reference window, weeks"); axs[0].set_title("Sharpe 2019-2026 by setting", fontsize=8)
axs[1].hist(PL.share_pos, bins=15, color="#888888", alpha=.7); axs[1].axvline(real["share_pos"], color="#c0392b", lw=2)
axs[1].set_xlabel("share of the 1,440 variants with Sharpe > 0"); axs[1].set_ylabel("placebo signals")
axs[1].set_title(f"{np.mean(PL.share_pos >= real['share_pos']):.0%} of placebos as good", fontsize=8)
axs[1].spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.savefig(os.path.join(FIG, "china_local_grid.png"), dpi=160); plt.close()
open(OUT + "china_local_robust_summary.txt", "w").write("\n".join(lines) + "\n")

# ---------------------------------------------------------------- 6. the variant picked on 2019-2022 only
# The grid's best variant on the training years is World share, L 52, hold 16, scale 1, vol target.
# Its 2023-2026 result is out of sample for that choice; the placebo above checks the whole procedure.
def rule_w(z, ret=r, hold=16, s=1, cost=4e-4):
    b = (-z / s).clip(-1, 1).rolling(hold, min_periods=1).mean()
    w = (b * 0.10 / T.past_vol(ret)).clip(-1, 1).fillna(0)
    return w * ret - w.diff().abs().fillna(0) * cost
nb = []
for h in [12, 16, 20, 26, 39]:
    for L in [13, 26, 52]:
        q = rule_w(Z[("World", L, 1)], hold=h).loc["2019":"2026"]
        nb.append(dict(hold=h, L=L, sharpe_all=sh(q), sharpe_train=sh(q.loc[:"2022"]), sharpe_test=sh(q.loc["2023":])))
nb = pd.DataFrame(nb); nb.to_csv(OUT + "china_local_world_lead.csv", index=False)
say("[world lead] neighbourhood (World share, scale 1, lag 1): Sharpe all / test\n" + nb.pivot(index="hold", columns="L", values="sharpe_all").round(2).to_string()
    + "\n" + nb.pivot(index="hold", columns="L", values="sharpe_test").round(2).to_string())
pw = rule_w(Z[("World", 52, 1)]).loc["2019":"2026"]
say("[world lead] by year " + ", ".join(f"{y} {sh(pw[pw.index.year == y]):+.2f}" for y in range(2020, 2027)) + f"; without Sep-Nov 2026 {sh(pw.loc[:'2026-08']):.2f}")
say(f"[world lead] costs 8 / 20 bp: {sh(rule_w(Z[('World', 52, 1)], cost=8e-4).loc['2019':'2026']):.2f} / {sh(rule_w(Z[('World', 52, 1)], cost=20e-4).loc['2019':'2026']):.2f}; on constant-maturity series {sh(rule_w(Z[('World', 52, 1)], ret=r_cm).loc['2019':'2026']):.2f}; same rule on SHFE vs LME {sh(rule_w(Z[('World', 52, 1)], ret=r_ratio).loc['2019':'2026']):.2f}")
x = pw.dropna().values; n = len(x); Bw = []
for _ in range(5000):
    st = rng.integers(0, n - 16, size=n // 16 + 1); y = np.concatenate([x[s:s + 16] for s in st])[:n]; Bw.append(y.mean() / y.std() * np.sqrt(52))
say(f"[world lead] Sharpe {sh(pw):.2f}, 16-week block bootstrap 90 % interval {np.percentile(Bw,5):.2f} to {np.percentile(Bw,95):.2f}, P(Sharpe <= 0) {np.mean(np.array(Bw) <= 0):.3f}; return {pw.mean()*52*100:.2f} %/yr at {pw.std()*np.sqrt(52)*100:.2f} % vol")
fig, ax = plt.subplots(figsize=(7.2, 3.0))
for lab, v, c in [("China rule, constant-maturity series (not tradable)", rule(z26, ret=r_cm).loc["2019":"2026"], "#bbbbbb"),
                  ("China rule, tradable contracts", p0, "#1f5fa8"), ("World share, L 52, hold 16 (picked on 2019-2022)", pw, "#c0392b")]:
    ax.plot((1 + v.fillna(0)).cumprod(), color=c, lw=1.2, label=lab)
ax.axvline(pd.Timestamp("2023-01-01"), color="#888", lw=.7, ls="--"); ax.axhline(1, color="#888", lw=.6)
ax.legend(frameon=False, fontsize=7); ax.set_ylabel("growth of 1"); ax.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.savefig(os.path.join(FIG, "china_local_tradable.png"), dpi=160); plt.close()
open(OUT + "china_local_robust_summary.txt", "w").write("\n".join(lines) + "\n")
