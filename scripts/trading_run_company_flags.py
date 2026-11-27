"""Backtest: trade the operator's share (vs its home index) on monthly satellite flags of its smelters.
Abnormal log returns over 20 and 40 trading days from month end + 7 days, for every company-month,
were computed from Yahoo daily closes in a browser (company_abnormal_returns_raw.json, bp)."""
import os, json, numpy as np, pandas as pd
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
raw=json.load(open(D+"trading/company_abnormal_returns_raw.json"))
months=pd.period_range("2019-01","2026-10",freq="M"); rows=[]
for c,s in raw.items():
    v=s.split(","); assert len(v)==len(months),(c,len(v))
    for m,x in zip(months,v):
        a,b=(x.split(":")+[""])[:2]
        rows.append(dict(company=c,month=str(m),ar4=float(a)/1e4 if a else np.nan,ar8=float(b)/1e4 if b else np.nan))
A=pd.DataFrame(rows); A.to_csv(D+"trading/company_abnormal_returns.csv",index=False)
F=pd.read_csv(D+"trading/company_flags_monthly.csv")[["company","month","signal","sites"]]
T=A.merge(F,on=["company","month"],how="left"); T["signal"]=T.signal.fillna(0)
rng=np.random.default_rng(0); out=[]
for h in ["ar4","ar8"]:
    cost=8e-4
    for per,m in [("2019-22",T.month<"2023"),("2023-26",T.month>="2023"),("all",T.month>"")]:
        d=T[m&T[h].notna()]; tr=d[d.signal!=0]; x=tr.signal*tr[h]-cost
        # permutation: reshuffle each company's signals across its own months
        sims=[]
        for _ in range(3000):
            s=d.groupby("company").signal.transform(lambda z: rng.permutation(z.values))
            sims.append(((s*d[h])[s!=0]-cost).mean())
        out.append(dict(horizon={"ar4":"4 weeks","ar8":"8 weeks"}[h],period=per,trades=len(x),mean_pct=100*x.mean(),hit=(x>0).mean(),
            t_stat=x.mean()/x.std()*np.sqrt(len(x)),perm_p=np.mean(np.array(sims)>=x.mean()),
            down_flags_mean_pct=100*(-tr[h][tr.signal<0]).mean(),up_flags_mean_pct=100*tr[h][tr.signal>0].mean()))
R=pd.DataFrame(out); pd.set_option("display.width",220); print(R.round(3).to_string()); R.to_csv(D+"trading/company_flag_backtest.csv",index=False)
# portfolio: each month, equal weight across active signals, 4-week hold -> monthly series
d=T[T.ar4.notna()&(T.signal!=0)].assign(p=lambda z: z.signal*z.ar4-8e-4).groupby("month").p.mean().reindex([str(m) for m in months]).fillna(0)
for per,(a,b) in [("2019-22",("2019","2022-12")),("2023-26",("2023","2026-12"))]:
    x=d[(d.index>=a)&(d.index<=b)]; print(per,"portfolio Sharpe %.2f, %.1f%%/yr"%(x.mean()/x.std()*np.sqrt(12),100*12*x.mean()))
print(T[T.signal!=0].assign(x=lambda z:z.signal*z.ar4).groupby("company").x.agg(["count","mean"]).round(4))
