"""Placebo check of the train/test Sharpe rank correlation in the fabricator search (20 time-shifted signal sets)."""
import os; os.environ.setdefault("ORBITCU_DATA","/mnt/project-files/data/")
src=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"trading_fabricator_search.py")).read()
src=src[:src.index("R,NETS=search")]; src=src.replace('os.path.dirname(os.path.abspath(__file__))',repr(os.path.dirname(os.path.abspath(__file__))))
exec(src)
R=pd.read_csv(D+"trading/fabricator_search_results.csv"); q=R[R.family=="satellite"]
print("real rank corr",round(spearmanr(q.sharpe_train,q.sharpe_test)[0],3))
rng=np.random.default_rng(11); satS={k:v for k,v in S.items() if k.startswith("sat_")}; rc=[]; t10=[]
for it in range(20):
    sh=int(rng.integers(52,300)); P={k:pd.Series(np.roll(v.values,sh),index=v.index) for k,v in satS.items()}
    p,_=search(P,"placebo"); p=p.dropna(subset=["sharpe_train","sharpe_test"]); rc.append(spearmanr(p.sharpe_train,p.sharpe_test)[0]); t10.append(p.nlargest(10,"sharpe_train").sharpe_test.mean())
rc=np.array(rc); print("placebo rank corr: median %.3f, p95 %.3f, share>=real %.2f"%(np.median(rc),np.percentile(rc,95),np.mean(rc>=0.15))); print("placebo top10 test",np.round(sorted(t10),2))
