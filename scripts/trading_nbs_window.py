"""Event test: trade copper only between the day the satellite month is ready (month end + 5 days)
and the Chinese NBS release of that month (about 11 days later)."""
import os, sys, numpy as np, pandas as pd
from scipy.stats import spearmanr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu import trading as T
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
w=pd.read_csv(D+"trading/nbs_windows.csv").reset_index(drop=True)
px=pd.read_csv(D+"trading/nbs_window_prices.csv")
rng=np.random.default_rng(0); out=[]
w["sd"]=w.sat_surprise.shift(1).rolling(12,min_periods=6).std()
print("sat surprise vs NBS deviation, these 68 months:", spearmanr(w.sat_surprise,w.nbs_dev))
for inst,col,f in [("HG","hg_usd_lb","comex_hg_weekly.csv"),("LME","lme_3m_usd_t","lme_cu_3m_weekly.csv")]:
    p=px[px.inst==inst].sort_values("k").reset_index(drop=True)
    wk=T.load_weekly_prices(D+"trading/"+f,col); vol=T.past_vol(T.weekly_returns(wk))
    d=w.copy(); d["ret"]=p.release_px/p.entry_px-1; d["ret_pre"]=p.pre_px/p.entry_px-1; d["ret_day"]=p.release_px/p.pre_px-1
    # volatility known at entry (weekly series, Monday on/before entry)
    ent=pd.to_datetime(p.entry_date); d["vol"]=[vol[:e].dropna().iloc[-1] for e in ent]
    size=(0.10/d.vol).clip(upper=1.0)
    for rule,sig in [("sign",-np.sign(d.sat_surprise)),("scaled",(-d.sat_surprise/(2*d.sd)).clip(-1,1))]:
        wgt=sig*size; net=wgt*d.ret-2*2e-4*wgt.abs()
        for per,m in [("2020-22",d.release<"2023"),("2023-26",d.release>="2023"),("all",d.release>"")]:
            m=m&sig.notna(); x=net[m]; s=sig[m]; r=d.ret[m]
            # permutation: shuffle the satellite signal across windows
            perm=[(rng.permutation(s.values)*size[m].values*r.values).mean() for _ in range(5000)]
            out.append(dict(inst=inst,rule=rule,period=per,n=len(x),mean_bp=1e4*x.mean(),hit=(x>0).mean(),
                sharpe_ann=x.mean()/x.std()*np.sqrt(10),total=(1+x).prod()-1,perm_p=np.mean(np.array(perm)>=(s*size[m]*r).mean()),
                ic_window=spearmanr(s,r)[0],ic_release_day=spearmanr(s,d.ret_day[m])[0]))
    lo=d.ret*size; out.append(dict(inst=inst,rule="long every window",period="all",n=len(lo),mean_bp=1e4*lo.mean(),hit=(lo>0).mean(),sharpe_ann=lo.mean()/lo.std()*np.sqrt(10),total=(1+lo).prod()-1))
    # does the official surprise itself move the price on release day?
    print(inst,"NBS surprise vs release-day return:",spearmanr(-d.nbs_dev,d.ret_day))
    d.to_csv(D+f"trading/nbs_window_trades_{inst}.csv",index=False)
R=pd.DataFrame(out); pd.set_option("display.width",200); print(R.round(3).to_string()); R.to_csv(D+"trading/nbs_window_summary.csv",index=False)
