"""Large strategy search on weekly copper futures (COMEX HG and LME 3-month).

Signals (known each Sunday, traded the Monday 8 days later, held one week):
  satellite: capacity running share (world, China, Asia ex-China, Europe+Russia, Americas+Africa),
             number of stopped smelters, stops and restarts seen in the last 4 / 13 weeks (event list);
  physical market (for comparison): LME stocks, LME cash-3M spread.
Transforms: level vs trailing median (z over 13/26/52 weeks) or change over 4/8/13 weeks (z over 52);
smoothing 1 or 4 weeks; direction +/-; position sign or clip(z/2); volatility target 10 %, max weight 1;
2 bp per unit turnover.
Selection on 2019-2022 only; evaluation on 2023-2026. Controls: rank correlation of training and test
Sharpe across all strategies, best-of-search placebo (satellite signals shifted in time), deflated Sharpe."""
import os, sys, itertools, numpy as np, pandas as pd
from scipy.stats import spearmanr, norm, skew, kurtosis
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu import trading as T
from orbitcu.sites import load_sites
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
sites={s.id:s for s in load_sites()}
REG={"China":["China"],"AsiaExCN":["Japan","South Korea","India","Indonesia","Iran"],"EuropeRU":["Poland","Germany","Bulgaria","Russia"],"AmericasAF":["Chile","Peru","DR Congo"]}
st=pd.read_csv(D+"activity_index/weekly_status.csv",parse_dates=["week_end"])
st["cap"]=st.site_id.map(lambda x:(sites[x].capacity_kt or 0) if x in sites else 0); st["country"]=st.site_id.map(lambda x:sites[x].country if x in sites else "")
k=st[st.status!="unknown"].assign(run=lambda z:(z.status=="running")*z.cap)
idx=pd.read_csv(D+"activity_index/weekly_index.csv",parse_dates=["week_end"]).set_index("week_end")
S={}
S["sat_world_share"]=idx.running_share.where(idx.coverage>=0.5)
for r,cs in REG.items():
    g=k[k.country.isin(cs)].groupby("week_end")[["run","cap"]].sum(); tot=sum(s.capacity_kt or 0 for s in sites.values() if s.country in cs)
    S[f"sat_{r}_share"]=(g.run/g.cap).where(g.cap>=0.5*tot)
S["sat_n_stopped"]=idx.n_stopped.astype(float).where(idx.coverage>=0.5)
ev=pd.read_csv(D+"activity_index/events.csv",parse_dates=["seen_date"]); ev=ev[ev.seen_date>="2019-07-01"]
weeks=idx.index
for e in ["stop","restart"]:
    d=ev[ev.event==e].seen_date; cnt=pd.Series(0.0,index=weeks)
    for x in d: cnt[cnt.index>=x]=cnt[cnt.index>=x]  # placeholder
    wk=pd.Series(1,index=d+pd.to_timedelta((6-d.dt.weekday)%7,unit="D")).groupby(level=0).sum().reindex(weeks,fill_value=0).astype(float)
    for w in [4,13]: S[f"sat_{e}s_{w}w"]=wk.rolling(w).sum().where(weeks>="2019-10-01")
S["sat_net_events_13w"]=S["sat_restarts_13w"]-S["sat_stops_13w"]
ph=pd.read_csv(D+"trading/lme_cu_spread_stocks_weekly.csv",parse_dates=["week_monday"]).set_index("week_monday")
ph.index=ph.index-pd.Timedelta(days=1)          # value of Monday m known at m: date it to the Sunday before, then the usual 1-week lag... use lag 0 for market data below
S["mkt_lme_stock"]=np.log(ph.lme_stock_t); S["mkt_spread"]=ph.cash_minus_3m_usd_t
PX={"HG":T.load_weekly_prices(D+"trading/comex_hg_weekly.csv","hg_usd_lb"),"LME":T.load_weekly_prices(D+"trading/lme_cu_3m_weekly.csv","lme_3m_usd_t")}
RET={k:T.weekly_returns(v) for k,v in PX.items()}; VOL={k:T.past_vol(v) for k,v in RET.items()}
TR=("2019-01-01","2022-12-31"); TE=("2023-01-01","2026-11-30")
def transforms(x):
    out={}
    for L in [13,26,52]:
        med=x.shift(1).rolling(L,min_periods=int(.6*L)).median(); dev=x-med
        out[f"lvl{L}"]=dev/dev.shift(1).rolling(52,min_periods=26).std()
    for c in [4,8,13]:
        ch=x-x.shift(c); out[f"chg{c}"]=ch/ch.shift(1).rolling(52,min_periods=26).std()
    return out
def sharpe(x):
    x=x.dropna(); return x.mean()/x.std()*np.sqrt(52) if len(x)>20 and x.std()>0 else np.nan
def run(z,inst,lag_weeks,pos,direction):
    r=RET[inst]; zz=T.to_trade_dates(z,lag_weeks).reindex(r.index)
    raw=(direction*zz/2).clip(-1,1) if pos=="scaled" else np.sign(direction*zz)
    w=(raw*0.10/VOL[inst]).clip(-1,1).fillna(0)
    net=w*r-w.diff().abs().fillna(0)*2e-4
    return net
def search(signals,tag):
    rows=[]; nets={}
    for name,x in signals.items():
        lag=0 if name.startswith("mkt_") else 1      # market data are known at once; satellite needs ERA5 (1 week)
        for tn,z in transforms(x.astype(float)).items():
            for sm in [1,4]:
                zs=z.rolling(sm,min_periods=1).mean() if sm>1 else z
                for inst,pos,dr in itertools.product(["HG","LME"],["sign","scaled"],[1,-1]):
                    net=run(zs,inst,lag-0 if lag else 0,pos,dr) if True else None
                    key=(name,tn,sm,inst,pos,dr)
                    rows.append(dict(signal=name,transform=tn,smooth=sm,instrument=inst,position=pos,direction=dr,
                        sharpe_train=sharpe(net.loc[TR[0]:TR[1]]),sharpe_test=sharpe(net.loc[TE[0]:TE[1]]),sharpe_all=sharpe(net.loc["2019":"2026"])))
                    if tag=="real": nets[key]=net
    return pd.DataFrame(rows),nets
R,NETS=search(S,"real"); R=R.dropna(subset=["sharpe_train","sharpe_test"])
R["family"]=np.where(R.signal.str.startswith("sat_"),"satellite","market")
print("strategies:",len(R), R.family.value_counts().to_dict())
for fam in ["satellite","market"]:
    q=R[R.family==fam]; rho=spearmanr(q.sharpe_train,q.sharpe_test)
    top=q.nlargest(10,"sharpe_train")
    print(f"\n[{fam}] train-vs-test Sharpe rank corr {rho[0]:.2f} (p {rho[1]:.3f}); test Sharpe: median {q.sharpe_test.median():.2f}, share > 0 {np.mean(q.sharpe_test>0):.2f}")
    print("top 10 on training -> test:"); print(top[["signal","transform","smooth","instrument","position","direction","sharpe_train","sharpe_test"]].round(2).to_string(index=False))
    print("mean test Sharpe of top 10:",round(top.sharpe_test.mean(),2),"| top 1% on train:",round(q[q.sharpe_train>=q.sharpe_train.quantile(.99)].sharpe_test.mean(),2))
R.to_csv(D+"trading/strategy_search_results.csv",index=False)
# placebo: shift every satellite signal by the same random number of weeks (keeps their joint shape), repeat the search, keep the best training Sharpe and its test Sharpe
rng=np.random.default_rng(3); pl=[]
satS={k:v for k,v in S.items() if k.startswith("sat_")}
for it in range(40):
    sh=int(rng.integers(52,300))
    P={k:pd.Series(np.roll(v.values,sh),index=v.index) for k,v in satS.items()}
    q,_=search(P,"placebo"); q=q.dropna(subset=["sharpe_train","sharpe_test"])
    pl.append(dict(best_train=q.sharpe_train.max(),top10_test=q.nlargest(10,"sharpe_train").sharpe_test.mean(),best_all=q.dropna().sharpe_all.max()))
PL=pd.DataFrame(pl); PL.to_csv(D+"trading/strategy_search_placebo.csv",index=False)
q=R[R.family=="satellite"]
print("\nplacebo (40 searches on time-shifted satellite signals): best training Sharpe median %.2f (real %.2f); top-10 test mean median %.2f (real %.2f); best full-sample Sharpe median %.2f (real %.2f), share of placebos >= real %.2f"%(
    PL.best_train.median(),q.sharpe_train.max(),PL.top10_test.median(),q.nlargest(10,"sharpe_train").sharpe_test.mean(),PL.best_all.median(),q.sharpe_all.max(),np.mean(PL.best_all>=q.sharpe_all.max())))
# deflated Sharpe ratio of the best full-sample satellite strategy (Bailey & Lopez de Prado 2014)
best=q.loc[q.sharpe_all.idxmax()]; key=tuple(best[["signal","transform","smooth","instrument","position","direction"]])
x=NETS[key].loc["2019":"2026"].dropna(); x=x[x.index>=x.ne(0).idxmax()]; n=len(x); srw=x.mean()/x.std()
N=len(q); var_sr=np.var(q.sharpe_all/np.sqrt(52)); emax=np.sqrt(var_sr)*((1-0.5772)*norm.ppf(1-1/N)+0.5772*norm.ppf(1-1/(N*np.e)))
dsr=norm.cdf((srw-emax)*np.sqrt(n-1)/np.sqrt(1-skew(x)*srw+(kurtosis(x,fisher=False)-1)/4*srw**2))
print("best full-sample satellite strategy:",key,"Sharpe %.2f; deflated Sharpe probability %.2f (needs > 0.95)"%(best.sharpe_all,dsr))
