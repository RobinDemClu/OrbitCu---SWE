"""Weekly copper trading backtest (see orbitcu.trading). Usage: ORBITCU_DATA=<data folder> python scripts/trading_backtest.py"""
import os, sys, itertools, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orbitcu import trading as T
from orbitcu.sites import load_sites
D=os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/")+"/"
idx=pd.read_csv(D+"activity_index/weekly_index.csv",parse_dates=["week_end"]).set_index("week_end")
world=idx.running_share.where(idx.coverage>=0.5)          # ignore the 2018-19 warm-up weeks
# China-only share from the per-site status
sites={s.id:s for s in load_sites()}
st=pd.read_csv(D+"activity_index/weekly_status.csv",parse_dates=["week_end"])
cn=[k for k,o in sites.items() if o.country=="China"]
s=st[st.site_id.isin(cn)&(st.status!="unknown")].copy(); s["cap"]=s.site_id.map(lambda x:sites[x].capacity_kt)
s["run"]=(s.status=="running")*s.cap; g=s.groupby("week_end")[["run","cap"]].sum()
china=(g.run/g.cap).where(g.cap>=0.5*sum(sites[k].capacity_kt for k in cn))
hg=T.load_weekly_prices(D+"trading/comex_hg_weekly.csv","hg_usd_lb")
lme=T.load_weekly_prices(D+"trading/lme_cu_3m_weekly.csv","lme_3m_usd_t")
TRAIN=("2019-01-01","2022-12-31"); TEST=("2023-01-01","2026-11-30")
def per(bt,p): return bt.loc[p[0]:p[1]]
def row(name,bt,p):
    b=per(bt,p); m=T.metrics(b.net,b.turnover); m["name"]=name; m["period"]=p[0][:4]+"-"+p[1][:4]; return m
# 1) choose parameters on the training years only
grid=[]
for lb,sm in itertools.product([26,52],[1,4]):
    bt=T.run_strategy(world,hg,lookback=lb,smooth=sm)
    m=T.metrics(per(bt,TRAIN).net); grid.append({"lookback":lb,"smooth":sm,"train_sharpe":m["sharpe"]})
grid=pd.DataFrame(grid); print(grid)
best=grid.loc[grid.train_sharpe.idxmax()]; LB,SM=int(best.lookback),int(best.smooth)
print("chosen", LB, SM)
# 2) evaluate
res=[]; curves={}
for sig_name,sig in [("world",world),("china",china)]:
    for inst,px in [("HG",hg),("LME",lme)]:
        bt=T.run_strategy(sig,px,lookback=LB,smooth=SM)
        curves[f"{sig_name}_{inst}"]=bt
        for p in (TRAIN,TEST,("2019-01-01","2026-11-30")):
            r=row(f"satellite {sig_name} / {inst}",bt,p); r["sharpe_ci"]=T.sharpe_ci(per(bt,p).net); res.append(r)
for inst,px in [("HG",hg),("LME",lme)]:
    for nm,tv in [("buy-and-hold 1x",None),("buy-and-hold 10% vol",0.10)]:
        bt=T.buy_and_hold(px,tv); curves[f"bh{'' if tv is None else '_vt'}_{inst}"]=bt
        for p in (TRAIN,TEST,("2019-01-01","2026-11-30")):
            r=row(f"{nm} / {inst}",bt,p); r["sharpe_ci"]=T.sharpe_ci(per(bt,p).net); res.append(r)
R=pd.DataFrame(res); pd.set_option("display.width",200)
print(R[["name","period","weeks","ann_return","ann_vol","sharpe","sharpe_ci","max_drawdown","hit_rate","turnover_per_year"]].round(3).to_string())
R.to_csv(D+"trading/backtest_summary.csv",index=False)
# 3) information coefficient: z vs next tradable week return (full sample, HG)
z=T.to_trade_dates(T.surprise_z(world,LB,SM),1); r=T.weekly_returns(hg)
j=pd.concat([z,r],axis=1).dropna()
from scipy.stats import spearmanr
for p in (TRAIN,TEST):
    jj=j.loc[p[0]:p[1]]; print("IC",p, len(jj), spearmanr(-jj.z,jj.ret))
# 4) lag sensitivity, cost sensitivity, placebo (signal shifted by random whole years)
sens=[]
for lag in [0,1,2,4]:
    bt=T.run_strategy(world,hg,LB,SM,lag_weeks=lag); sens.append({"test":"lag_weeks","value":lag,"sharpe_test":T.metrics(per(bt,TEST).net)["sharpe"],"sharpe_all":T.metrics(per(bt,("2019","2026")).net)["sharpe"]})
for c in [0,2,10,20]:
    bt=T.run_strategy(world,hg,LB,SM,cost_bps=c); sens.append({"test":"cost_bps","value":c,"sharpe_test":T.metrics(per(bt,TEST).net)["sharpe"],"sharpe_all":T.metrics(per(bt,("2019","2026")).net)["sharpe"]})
rng=np.random.default_rng(1); pl=[]
base=world.dropna()
for k in range(500):
    sh=int(rng.integers(26,len(base)-26)); perm=pd.Series(np.roll(base.values,sh),index=base.index)
    bt=T.run_strategy(perm,hg,LB,SM); pl.append(T.metrics(per(bt,TEST).net)["sharpe"])
real=T.metrics(per(curves["world_HG"],TEST).net)["sharpe"]
print("placebo test Sharpe 95th pct %.2f, real %.2f, share of placebos >= real %.3f"%(np.percentile(pl,95),real,np.mean(np.array(pl)>=real)))
sens.append({"test":"placebo_p","value":np.nan,"sharpe_test":float(np.mean(np.array(pl)>=real)),"sharpe_all":np.nan})
pd.DataFrame(sens).to_csv(D+"trading/sensitivity.csv",index=False); print(pd.DataFrame(sens).round(3))
out=pd.DataFrame({k:v.net for k,v in curves.items()}); out["weight_world_HG"]=curves["world_HG"].weight
out.to_csv(D+"trading/weekly_returns.csv")
json.dump({"lookback":LB,"smooth":SM,"z_full":2.0,"lag_weeks":1,"target_vol":0.10,"max_weight":1.0,"cost_bps":2.0,"train":TRAIN,"test":TEST},open(D+"trading/chosen_params.json","w"),indent=1)
