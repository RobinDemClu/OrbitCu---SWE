"""Does the satellite supply surprise anticipate the physical market: LME stocks and the cash-3M spread?"""
import os, sys, numpy as np, pandas as pd
from scipy.stats import spearmanr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu import trading as T
from orbitcu.sites import load_sites
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
idx=pd.read_csv(D+"activity_index/weekly_index.csv",parse_dates=["week_end"]).set_index("week_end")
world=idx.running_share.where(idx.coverage>=0.5)
sites={s.id:s for s in load_sites()}
st=pd.read_csv(D+"activity_index/weekly_status.csv",parse_dates=["week_end"]); cn=[k for k,o in sites.items() if o.country=="China"]
s=st[st.site_id.isin(cn)&(st.status!="unknown")].copy(); s["cap"]=s.site_id.map(lambda x:sites[x].capacity_kt)
s["run"]=(s.status=="running")*s.cap; g=s.groupby("week_end")[["run","cap"]].sum()
china=(g.run/g.cap).where(g.cap>=0.5*sum(sites[k].capacity_kt for k in cn))
ph=pd.read_csv(D+"trading/lme_cu_spread_stocks_weekly.csv",parse_dates=["week_monday"]).set_index("week_monday")
lme=T.load_weekly_prices(D+"trading/lme_cu_3m_weekly.csv","lme_3m_usd_t")
rows=[]
for name,sig in [("world",world),("china",china)]:
    z=T.to_trade_dates(T.surprise_z(sig,26,1),1)          # known at this Monday
    for h in [4,8]:
        dstock=np.log(ph.lme_stock_t.shift(-h)/ph.lme_stock_t)
        dspread=(ph.cash_minus_3m_usd_t.shift(-h)-ph.cash_minus_3m_usd_t)/lme
        j=pd.concat([z.rename("z"),dstock.rename("dstock"),dspread.rename("dspread")],axis=1).dropna()
        for per,m in [("2019-22",j.index<"2023"),("2023-26",j.index>="2023"),("all",j.index>"2000")]:
            jj=j[m].iloc[::h]                                # non-overlapping windows for honest p-values
            a=spearmanr(jj.z,jj.dstock); b=spearmanr(-jj.z,jj.dspread)
            rows.append(dict(signal=name,h_weeks=h,period=per,n=len(jj),rho_z_vs_stock_change=a[0],p_stock=a[1],rho_short_supply_vs_spread_change=b[0],p_spread=b[1]))
R=pd.DataFrame(rows); pd.set_option("display.width",200); print(R.round(3).to_string()); R.to_csv(D+"trading/physical_market_tests.csv",index=False)
# spread trade: long cash / short 3M when supply is short (weight = clip(-z/2)), weekly P&L ~ change in spread / price, 2 bp per leg
for name,sig in [("world",world),("china",china)]:
    w=T.to_trade_dates(T.raw_position(T.surprise_z(sig,26,1)),1).reindex(ph.index).fillna(0)
    pnl=w*(ph.cash_minus_3m_usd_t.shift(-1)-ph.cash_minus_3m_usd_t)/lme - w.diff().abs().fillna(0)*4e-4
    for per in [("2019","2022"),("2023","2026")]:
        x=pnl.loc[per[0]:per[1]].dropna(); print(f"spread trade {name} {per}: Sharpe {x.mean()/x.std()*np.sqrt(52):.2f}, mean {1e4*x.mean():.2f} bp/week")
