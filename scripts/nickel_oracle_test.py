"""Oracle test: would perfect knowledge of supply help trade nickel, and copper?

We do not have monthly official nickel output (INSG monthly data are for subscribers), so the market
balance is measured by LME warehouse stocks: a supply surplus ends up in stocks. Questions:
 1. same-period link: over 4 / 13 / 26 weeks, how much do price moves follow stock moves?
 2. perfect-foresight strategy: knowing the stock change of the next h weeks, go short if stocks will
    rise, long if they will fall (10 % vol target, 2 bp). Then a signal that is only correlated rho with
    that future change (truth + noise), to see what accuracy is needed.
 3. is the information already in public data? same strategy on the PAST h-week stock change.
Nickel: LME nickel 3-month and stocks (Westmetall); copper: same for copper.
Event study on dated Indonesian supply shocks is in nickel_supply_events.py."""
import numpy as np, pandas as pd, os, sys
from scipy.stats import spearmanr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu import trading as T
D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/"
ni = pd.read_csv(D + "nickel/lme_ni_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
cu = pd.read_csv(D + "trading/lme_cu_spread_stocks_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
cup = pd.read_csv(D + "trading/lme_cu_3m_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday").lme_3m_usd_t
M = {"nickel": (ni.lme_ni_3m_usd_t, ni.lme_ni_stock_t), "copper": (cup, cu.lme_stock_t)}
rows = []; rng = np.random.default_rng(0)
def sharpe(x):
    x = x.dropna(); return x.mean() / x.std() * np.sqrt(52)
for m, (p, s) in M.items():
    p = p.astype(float).loc["2018":]; s = np.log(s.astype(float).reindex(p.index).ffill())
    p = p.where(p.index != "2022-03-07")          # LME nickel suspended and trades cancelled in March 2022
    r = T.weekly_returns(p.ffill()); vol = T.past_vol(r)
    for h in [4, 13, 26]:
        ds, dp = s.shift(-h) - s, np.log(p.ffill()).shift(-h) - np.log(p.ffill())
        x = pd.concat([ds, dp], axis=1).dropna().iloc[::h]; rho, pv = spearmanr(x.iloc[:, 0], x.iloc[:, 1])
        # weekly strategy: signal known at Monday m = stock change over [m, m+h] (oracle) or [m-h, m] (public)
        def strat(sig):
            w = (-np.sign(sig) * 0.10 / vol).clip(-1, 1).fillna(0); return w * r - w.diff().abs().fillna(0) * 2e-4
        z = (ds - ds.mean()) / ds.std()
        noisy = {}
        for target in [0.2, 0.3, 0.5]:
            sh = []
            for _ in range(50):
                e = pd.Series(rng.standard_normal(len(z)), index=z.index)
                sig = target * z + np.sqrt(1 - target ** 2) * e
                sh.append(sharpe(strat(sig.where(z.notna())).loc["2019":"2026"]))
            noisy[f"sharpe_if_rho_{target}"] = np.median(sh)
        rows.append(dict(metal=m, horizon_weeks=h, same_period_rho=rho, p_value=pv, n=len(x),
                         sharpe_oracle=sharpe(strat(ds).loc["2019":"2026"]), **noisy,
                         sharpe_public_past=sharpe(strat(s - s.shift(h)).loc["2019":"2026"])))
R = pd.DataFrame(rows); R.to_csv(D + "nickel/oracle_test.csv", index=False); print(R.round(2).to_string(index=False))
# how idiosyncratic is each metal? correlation of weekly returns with the other base metals
al = pd.read_csv(D + "trading/lme_al_zn_3m_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
ret = pd.DataFrame({"nickel": T.weekly_returns(ni.lme_ni_3m_usd_t.astype(float)), "copper": T.weekly_returns(cup.astype(float)),
                    "aluminium": T.weekly_returns(al.lme_al_3m_usd_t), "zinc": T.weekly_returns(al.lme_zn_3m_usd_t)}).loc["2019":].drop(pd.Timestamp("2022-02-28"), errors="ignore").drop(pd.Timestamp("2022-03-07"), errors="ignore")
print(ret.corr().round(2))
