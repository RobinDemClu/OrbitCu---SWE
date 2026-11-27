"""Do the per-plant surprise flags (VIIRS, else share of hot images) lead to copper price moves?
Two questions: (1) does the market react when a large company surprise is published?
(2) does trading on the satellite flag, from the decision time, earn money?"""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu import trading as T
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
a=pd.read_csv(D+"production/images/surprise_r.csv"); b=pd.read_csv(D+"production/images/surprise_r_img.csv")
k=["entity","period_start","period_end"]
t=a.merge(b[k+["flag"]].rename(columns={"flag":"flag_img"}),on=k,how="left")
t["flag_comb"]=np.where(t.flag!=0,t.flag,t.flag_img.fillna(0)).astype(int)
for c in ["decision_time","published_at"]: t[c]=pd.to_datetime(t[c],utc=True,format="mixed").dt.tz_localize(None).dt.normalize()
mon=lambda s: s+pd.to_timedelta((7-s.dt.weekday)%7,unit="D")
t["entry"]=mon(t.decision_time); t["pub_monday"]=mon(t.published_at)
print("flags:",(t.flag_comb!=0).sum(),"hits:",((t.flag_comb!=0)&(t.flag_comb==t.surprise)).sum(),"surprises:",(t.surprise!=0).sum())
rows=[]
for inst,col,f in [("HG","hg_usd_lb","comex_hg_weekly.csv"),("LME","lme_3m_usd_t","lme_cu_3m_weekly.csv")]:
    lp=np.log(T.load_weekly_prices(D+"trading/"+f,col)); base={h:(lp.shift(-h)-lp) for h in [1,2,4,8]}
    # (1) reaction to the published surprise: return over the publication week, sign = -surprise (less output -> up)
    pw=lp.shift(-1)-lp
    pub_prev=t.pub_monday-pd.Timedelta(days=7)
    s=t[t.surprise!=0]; r=pw.reindex(pub_prev[s.index]).values
    x=-s.surprise.values*r; x=x[~np.isnan(x)]
    rows.append(dict(inst=inst,test="published surprise, week of publication",n=len(x),mean_pct=100*x.mean(),hit=(x>0).mean(),t_stat=x.mean()/x.std()*np.sqrt(len(x))))
    # (2) trade the flag from the decision time: long if flag down (-1), short if up
    fl=t[t.flag_comb!=0]
    for h in [1,2,4,8]:
        r=base[h].reindex(fl.entry).values; x=-fl.flag_comb.values*r-4e-4; x=x[~np.isnan(x)]
        rows.append(dict(inst=inst,test=f"trade satellite flag, {h} weeks",n=len(x),mean_pct=100*x.mean(),hit=(x>0).mean(),t_stat=x.mean()/x.std()*np.sqrt(len(x)) if len(x)>1 else np.nan))
    # until publication
    rr=[(lp.get(p,np.nan)-lp.get(e,np.nan)) for e,p in zip(fl.entry,fl.pub_monday)]
    x=-fl.flag_comb.values*np.array(rr)-4e-4; x=x[~np.isnan(x)]
    rows.append(dict(inst=inst,test="trade satellite flag, until publication",n=len(x),mean_pct=100*x.mean(),hit=(x>0).mean(),t_stat=x.mean()/x.std()*np.sqrt(len(x))))
R=pd.DataFrame(rows); pd.set_option("display.width",200); print(R.round(2).to_string()); R.to_csv(D+"trading/surprise_flag_tests.csv",index=False)
print(t.loc[t.flag_comb!=0,["entity","period_end","decision_time","published_at","surprise","flag_comb"]].to_string())
