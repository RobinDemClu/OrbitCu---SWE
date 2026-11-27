"""Three more strategy families, same signals and rules as trading_strategy_search.py.

A. Relative value: copper against aluminium and zinc (LME 3-month, Westmetall). A copper-only supply
   shock should move copper against other base metals, while world demand moves them all; the hedge
   removes much of the demand noise. Instruments: Cu-Al, Cu-Zn, Cu minus the mean of Al and Zn
   (weekly return of copper minus return of the other leg).
B. Combined model: all satellite features at once (15 signals x 6 transforms), ridge regression on the
   next week's return, refitted every January on all data before it (expanding window), so every
   prediction from 2021 on is out of sample. Position = sign or clip(prediction / its past std / 2).
C. Risk timing: do satellite signals forecast copper volatility? Out-of-sample R^2 of a 4-week absolute
   return forecast with and without satellite features, and a long-only COMEX strategy sized by the
   forecast against plain volatility targeting.
Controls: time-shifted placebo signals for every family."""
import os, numpy as np, pandas as pd
from scipy.stats import spearmanr
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ORBITCU_DATA", "/mnt/project-files/data/")
src = open(os.path.join(HERE, "trading_fabricator_search.py")).read()
src = src[:src.index("R,NETS=search")].replace("os.path.dirname(os.path.abspath(__file__))", repr(HERE))
exec(src)  # signals S, transforms(), sharpe(), search(), D, T, TR, TE

OUT = D + "trading/"
lme = pd.read_csv(D + "trading/lme_al_zn_3m_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
cu = T.load_weekly_prices(D + "trading/lme_cu_3m_weekly.csv", "lme_3m_usd_t")
hg = T.load_weekly_prices(D + "trading/comex_hg_weekly.csv", "hg_usd_lb")
rcu, ral, rzn = T.weekly_returns(cu), T.weekly_returns(lme.lme_al_3m_usd_t), T.weekly_returns(lme.lme_zn_3m_usd_t)
rhg = T.weekly_returns(hg)
satS = {k: v for k, v in S.items() if k.startswith("sat_")}
rng = np.random.default_rng(5)
SHIFTS = [int(rng.integers(52, 300)) for _ in range(30)]
def shifted(sig, sh): return {k: pd.Series(np.roll(v.values, sh), index=v.index) for k, v in sig.items()}
lines = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); lines.append(s)

# ---------------------------------------------------------------- A. relative value
RET.clear(); RET.update({"CuAl": (rcu - ral).rename("ret"), "CuZn": (rcu - rzn).rename("ret"),
                         "CuBase": (rcu - (ral + rzn) / 2).rename("ret")})
INSTR[:] = list(RET); VOL.clear(); VOL.update({k: T.past_vol(v) for k, v in RET.items()})
A, _ = search(satS, "real"); A = A.dropna(subset=["sharpe_train", "sharpe_test"])
A.to_csv(OUT + "relative_value_search.csv", index=False)
rho = spearmanr(A.sharpe_train, A.sharpe_test)[0]; top = A.nlargest(10, "sharpe_train")
say(f"[A] {len(A)} strategies Cu vs Al/Zn: rank corr {rho:.2f}; median test {A.sharpe_test.median():.2f}; top10 test {top.sharpe_test.mean():.2f}; best all {A.sharpe_all.max():.2f}")
say(top[["signal", "transform", "smooth", "instrument", "position", "direction", "sharpe_train", "sharpe_test"]].round(2).to_string(index=False))
pa = []
for sh in SHIFTS:
    q, _ = search(shifted(satS, sh), "p"); q = q.dropna(subset=["sharpe_train", "sharpe_test"])
    pa.append((spearmanr(q.sharpe_train, q.sharpe_test)[0], q.nlargest(10, "sharpe_train").sharpe_test.mean(), q.sharpe_all.max()))
pa = np.array(pa)
say(f"[A] placebo: rank corr median {np.median(pa[:,0]):.2f} (share >= real {np.mean(pa[:,0]>=rho):.2f}); top10 test median {np.median(pa[:,1]):.2f} (share >= real {np.mean(pa[:,1]>=top.sharpe_test.mean()):.2f}); best all median {np.median(pa[:,2]):.2f} (share >= real {np.mean(pa[:,2]>=A.sharpe_all.max()):.2f})")

# ---------------------------------------------------------------- B. combined walk-forward model
def features(sig):
    F = {}
    for n, x in sig.items():
        for tn, z in transforms(x.astype(float)).items():
            F[f"{n}|{tn}"] = T.to_trade_dates(z, 1)
    return pd.DataFrame(F)
def walk_forward(F, r, lam=10.0):
    F = F.reindex(r.index).clip(-4, 4); pred = pd.Series(np.nan, index=r.index)
    for yr in range(2021, 2027):
        tr = (F.index < f"{yr}-01-01") & (F.index >= "2019-07-01"); te = (F.index >= f"{yr}-01-01") & (F.index < f"{yr+1}-01-01")
        X = F[tr].fillna(0).to_numpy(); y = r[tr].fillna(0).to_numpy()
        if len(y) < 52: continue
        mu, sd = X.mean(0), X.std(0) + 1e-9; Xs = (X - mu) / sd
        b = np.linalg.solve(Xs.T @ Xs + lam * len(y) * np.eye(Xs.shape[1]), Xs.T @ (y - y.mean()))
        pred[te] = ((F[te].fillna(0).to_numpy() - mu) / sd) @ b
    return pred
def trade(pred, r, pos):
    z = pred / pred.shift(1).rolling(52, min_periods=20).std()
    raw = np.sign(z) if pos == "sign" else (z / 2).clip(-1, 1)
    w = (raw * 0.10 / T.past_vol(r)).clip(-1, 1).fillna(0)
    return w * r - w.diff().abs().fillna(0) * 2e-4
B = []
targets = {"HG": rhg, "LME": rcu, "CuBase": (rcu - (ral + rzn) / 2)}
F_real = features(satS)
FP = {sh: features(shifted(satS, sh)) for sh in SHIFTS[:20]}
for tname, r in targets.items():
    for lam in [1.0, 10.0, 100.0]:
        p = walk_forward(F_real, r, lam)
        for pos in ["sign", "scaled"]:
            net = trade(p, r, pos).loc["2021":"2026"]
            ic = spearmanr(p.loc["2021":"2026"], r.loc["2021":"2026"], nan_policy="omit")[0]
            pl = []
            for sh in SHIFTS[:20]:
                pp = walk_forward(FP[sh], r, lam); pl.append(sharpe(trade(pp, r, pos).loc["2021":"2026"]))
            B.append(dict(target=tname, ridge_lambda=lam, position=pos, sharpe_2021_2026=sharpe(net), ic=ic,
                          sharpe_2023_2026=sharpe(net.loc["2023":]), placebo_median=np.nanmedian(pl), placebo_share_ge=np.mean(np.array(pl) >= sharpe(net))))
B = pd.DataFrame(B); B.to_csv(OUT + "combined_model.csv", index=False)
say("[B] combined ridge model, out of sample 2021-2026:"); say(B.round(2).to_string(index=False))

# ---------------------------------------------------------------- C. risk timing
fut = rhg.abs().rolling(4).mean().shift(-3)            # mean |weekly return| over the 4 weeks starting at m
base = rhg.abs().shift(1).rolling(26, min_periods=13).mean()
def vol_forecast(F, use_sat):
    X0 = pd.DataFrame({"base": base}); X = pd.concat([X0, F.reindex(rhg.index).clip(-4, 4)], axis=1) if use_sat else X0
    pred = pd.Series(np.nan, index=rhg.index)
    for yr in range(2021, 2027):
        tr = (X.index < f"{yr}-01-01") & (X.index >= "2019-07-01") & fut.notna() & (X.index <= pd.Timestamp(f"{yr}-01-01") - pd.Timedelta(days=28))
        te = (X.index >= f"{yr}-01-01") & (X.index < f"{yr+1}-01-01")
        Xa = X[tr].fillna(0).to_numpy(); y = fut[tr].to_numpy(); mu, sd = Xa.mean(0), Xa.std(0) + 1e-9; Xs = np.c_[np.ones(len(y)), (Xa - mu) / sd]
        reg = 10.0 * len(y) * np.eye(Xs.shape[1]); reg[0, 0] = 0; reg[1, 1] = 0   # no penalty on intercept and past vol
        b = np.linalg.solve(Xs.T @ Xs + reg, Xs.T @ y)
        pred[te] = np.c_[np.ones(te.sum()), (X[te].fillna(0).to_numpy() - mu) / sd] @ b
    return pred.clip(lower=1e-3)
def r2(p):
    m = p.notna() & fut.notna() & (p.index >= "2021-01-01"); e = fut[m] - p[m]; e0 = fut[m] - base[m] * (fut[m].mean() / base[m].mean())
    return 1 - (e ** 2).sum() / ((fut[m] - fut[m].mean()) ** 2).sum()
def sized(p):
    w = (0.10 / (p * np.sqrt(52) * np.sqrt(np.pi / 2))).clip(0, 1).fillna(0)
    return (w * rhg - w.diff().abs().fillna(0) * 2e-4).loc["2021":"2026"]
p0, p1 = vol_forecast(F_real, False), vol_forecast(F_real, True)
pl = [r2(vol_forecast(FP[sh], True)) for sh in SHIFTS[:20]]
C = dict(r2_past_vol_only=r2(p0), r2_with_satellite=r2(p1), r2_placebo_median=np.median(pl), r2_placebo_share_ge=np.mean(np.array(pl) >= r2(p1)),
         sharpe_sized_past_vol=sharpe(sized(p0)), sharpe_sized_satellite=sharpe(sized(p1)))
pd.Series(C).to_csv(OUT + "risk_timing.csv", header=["value"])
say("[C] risk timing 2021-2026:", {k: round(v, 3) for k, v in C.items()})
open(OUT + "explore_more_summary.txt", "w").write("\n".join(lines) + "\n")
