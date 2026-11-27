"""Fast VIIRS stop alerts (build_fast_alerts.py): copper (COMEX HG) and operator-share abnormal returns
1, 5, 10 and 20 trading days after the close of the day the alert (or the restart) is known.
Returns were computed from Yahoo daily closes in a browser (fast_alert_returns_raw.txt, bp)."""
import os, numpy as np, pandas as pd
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
A=pd.read_csv(D+"trading/fast_alerts.csv"); raw=open(D+"trading/fast_alert_returns_raw.txt").read().strip().split(",")
assert len(raw)==len(A)
H=[1,5,10,20]; rows=[]
def parse(s):
    if not s: return [np.nan]*4
    return [float(v)/1e4 if v!="" else np.nan for v in s.split("/")]
for (i,a),r in zip(A.iterrows(),raw):
    for ev,part in zip(["stop","restart"],r.split(";")):
        hg,eq=(part.split("|")+[""])[:2]
        rows.append(dict(site_id=a.site_id,event=ev,gap_days=a.gap_days,**{f"hg_{h}":v for h,v in zip(H,parse(hg))},**{f"eq_{h}":v for h,v in zip(H,parse(eq))}))
R=pd.DataFrame(rows); R.to_csv(D+"trading/fast_alert_returns.csv",index=False)
out=[]
for ev,sg_hg,sg_eq in [("stop",1,-1),("restart",-1,1)]:      # stop: copper up, operator share down
    for grp,m in [("all alerts",R.event==ev),("gap >= 21 days (known only afterwards)",(R.event==ev)&(R.gap_days>=21))]:
        d=R[m]
        for h in H:
            for k,sg,cost in [("hg",sg_hg,4e-4),("eq",sg_eq,8e-4)]:
                x=(sg*d[f"{k}_{h}"]).dropna()-cost
                if len(x)>2: out.append(dict(event=ev,group=grp,asset={"hg":"COMEX copper","eq":"operator share vs index"}[k],days=h,n=len(x),mean_pct=100*x.mean(),hit=(x>0).mean(),t=x.mean()/x.std()*np.sqrt(len(x))))
O=pd.DataFrame(out); pd.set_option("display.width",220); print(O.round(2).to_string()); O.to_csv(D+"trading/fast_alert_tests.csv",index=False)
