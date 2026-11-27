"""Fast stop alerts from daily VIIRS detections only (no cloud data needed, so ready the next morning).
A site is 'usually hot' if hot on >= 40 % of the previous 365 days. An alert fires on the first day the
current run of days without any detection is longer than the 99th percentile of the site's runs in the
previous 3 years (at least 5 days): clouds rarely hide a hot furnace that long. The alert ends at the
next detection (restart). Alerts from both VIIRS satellites combined."""
import os, numpy as np, pandas as pd
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
def days(path):
    m=pd.read_csv(path); out=[]
    for r in m.itertuples():
        p=pd.Period(r.month,freq="M"); bits=int(str(r.mask),16)
        for d in range(1,p.days_in_month+1):
            out.append((r.site_id,p.start_time+pd.Timedelta(days=d-1),(bits>>(d-1))&1))
    return pd.DataFrame(out,columns=["site_id","date","hot"])
a=days(D+"production/noaa20_site_masks.csv"); b=days(D+"production/snpp_site_masks.csv")
x=pd.concat([a,b]).groupby(["site_id","date"]).hot.max().reset_index()
alerts=[]
for s,g in x.groupby("site_id"):
    g=g.set_index("date").hot.asfreq("D",fill_value=0)
    share=g.shift(1).rolling(365,min_periods=300).mean()
    run=g.eq(0).groupby(g.ne(0).cumsum()).cumsum()        # current gap length
    ends=run[(run.shift(-1)==0)&(run>0)]                   # completed gaps (value = length) on last day
    in_alert=False
    for d in g.index[400:]:
        if in_alert:
            if g[d]==1: alerts[-1]["restart"]=d; in_alert=False
            continue
        past=ends[(ends.index<d-pd.Timedelta(days=1))&(ends.index>=d-pd.Timedelta(days=3*365))]
        if len(past)<20 or share.get(d,0)<0.4: continue
        thr=max(5,int(np.ceil(np.percentile(past,99))))
        if run[d]==thr:
            alerts.append(dict(site_id=s,gap_start=d-pd.Timedelta(days=thr-1),alert_date=d,threshold_days=thr,share=share[d])); in_alert=True
A=pd.DataFrame(alerts); A["known"]=A.alert_date+pd.Timedelta(days=1)
A["restart_known"]=pd.to_datetime(A.get("restart"))+pd.Timedelta(days=1)
A["gap_days"]=(pd.to_datetime(A.get("restart"))-A.gap_start).dt.days
A.to_csv(D+"trading/fast_alerts.csv",index=False)
print(len(A)); print(A.groupby("site_id").size().to_dict()); print(A.gap_days.describe().round(1).to_dict()); print(A.threshold_days.describe().round(1).to_dict())
