"""Event study: copper price after each smelter stop / restart seen by the satellites."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu import trading as T
from orbitcu.sites import load_sites
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
sites={s.id:s for s in load_sites()}
e=pd.read_csv(D+"activity_index/events.csv",parse_dates=["seen_date"])
e=e[(e.seen_date>="2019-07-01")&e.event.isin(["stop","restart"])].copy()
e["cap"]=e.site_id.map(lambda x: sites[x].capacity_kt if x in sites else np.nan)
e["entry"]=e.seen_date+pd.to_timedelta((7-e.seen_date.dt.weekday)%7,unit="D")   # first Monday on/after the day it is known
H=[1,2,4,8]; rng=np.random.default_rng(0); rows=[]
for inst,col,f in [("HG","hg_usd_lb","comex_hg_weekly.csv"),("LME","lme_3m_usd_t","lme_cu_3m_weekly.csv")]:
    p=T.load_weekly_prices(D+"trading/"+f,col); lp=np.log(p)
    fwd={h:(lp.shift(-h)-lp) for h in H}
    pool=lp.loc["2019-07-01":].index
    for ev,sgn in [("stop",1),("restart",-1)]:
        for grp,m in [("all",slice(None)),("capacity >= 400 kt",None)]:
            sub=e[e.event==ev]; sub=sub if m is not None else sub[sub.cap>=400]
            for h in H:
                r=fwd[h].reindex(sub.entry).dropna()
                base=fwd[h].loc[pool].dropna()
                excess=r.mean()-base.mean()
                # random-date test: same number of random Mondays, 5000 draws
                sims=np.array([base.sample(len(r),random_state=int(s)).mean()-base.mean() for s in rng.integers(0,1e9,3000)])
                p_one=np.mean(sgn*sims>=sgn*excess)
                rows.append(dict(inst=inst,event=ev,group=grp,h_weeks=h,n=len(r),mean_ret_pct=100*r.mean(),excess_pct=100*excess,hit_expected=np.mean(sgn*(r-base.mean())>0),p_value=p_one))
R=pd.DataFrame(rows); pd.set_option("display.width",200); print(R.round(3).to_string()); R.to_csv(D+"trading/event_study.csv",index=False)
