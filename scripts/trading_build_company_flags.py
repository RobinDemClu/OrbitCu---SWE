"""Monthly satellite flags for every smelter with a listed operator (no published figure needed).
VIIRS: r = hot share of clear days this month / median of the previous 12 months (site visible if that
median >= 0.3 and >= 5 clear days); flag down if r < 0.6, up if r > 1.3 (thresholds of the production model).
Else images: hot share of usable images vs previous 12 months (>= 3 usable images, ref >= 0.2), 0.75 / 1.33.
Flags are aggregated per operator and month (capacity-weighted sign) and dated month end + 7 days."""
import os, numpy as np, pandas as pd
D=os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/"
OP={"CN-GUIXI":"Jiangxi Copper","KR-ONSAN":"LS Corp","CD-KAMOA":"Ivanhoe","IN-KUTCH":"Adani Ent.","IN-DAHEJ":"Hindalco",
    "CN-JINGUAN":"Tongling","CN-JINLONG":"Tongling","PL-GLOGOW":"KGHM","PL-LEGNICA":"KGHM","DE-HAMBURG":"Aurubis","BG-PIRDOP":"Aurubis",
    "JP-TOYO":"Sumitomo MM","CN-CHIFENG-YJ":"Yunnan Copper","CN-SOUTHEAST":"Yunnan Copper","PE-ILO":"Southern Copper",
    "ID-GRESIK":"Freeport","ID-MANYAR":"Freeport","CN-DAYE":"China Daye","JP-ONAHAMA":"Mitsubishi Materials"}
CAP={"PL-LEGNICA":160,"ID-MANYAR":480}
a=pd.read_parquet(D+"production/act.parquet"); a=a[a.site_id.isin(OP)].copy()
a["month"]=pd.PeriodIndex(a.month,freq="M"); a=a.sort_values(["site_id","month"])
def ratio(g,col,ok,minref):
    ref=g[col].where(ok).shift(1).rolling(12,min_periods=8).median()
    return (g[col]/ref).where(ok&(ref>=minref))
out=[]
for s,g in a.groupby("site_id"):
    g=g.set_index("month"); ok=g.clear_days>=5
    out.append(pd.DataFrame({"site_id":s,"r_viirs":ratio(g,"hot_frac",ok,0.3)}))
v=pd.concat(out).reset_index()
im=pd.read_csv(D+"image_scores/image_scores_monthly.csv"); lg=D+"image_scores/legnica/"
im=im[im.site_id.isin(OP)].copy(); im["month"]=pd.PeriodIndex(im.month,freq="M")
o2=[]
for s,g in im.sort_values("month").groupby("site_id"):
    g=g.set_index("month"); ok=g.n_usable>=3
    o2.append(pd.DataFrame({"site_id":s,"r_img":ratio(g,"hot_share",ok,0.2)}))
im=pd.concat(o2).reset_index()
f=v.merge(im,on=["site_id","month"],how="outer")
fv=np.where(f.r_viirs<0.6,-1,np.where(f.r_viirs>1.3,1,0)); fi=np.where(f.r_img<0.75,-1,np.where(f.r_img>1.33,1,0))
f["flag"]=np.where(f.r_viirs.notna(),fv,fi)
import sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from orbitcu.sites import load_sites
caps={x.id:(x.capacity_kt or 0) for x in load_sites()}; caps.update(CAP)
f["company"]=f.site_id.map(OP); f["cap"]=f.site_id.map(caps)
f=f[(f.month>=pd.Period("2019-01","M"))&(f.month<=pd.Period("2026-10","M"))]
c=f.assign(w=f.flag*f.cap).groupby(["company","month"]).agg(w=("w","sum"),n_flag=("flag",lambda x:(x!=0).sum()),sites=("site_id",lambda x:"+".join(sorted(set(x))))).reset_index()
c["signal"]=np.sign(c.w).astype(int); c=c[c.signal!=0].copy()
c["entry"]=(c.month.dt.to_timestamp(how="end").dt.normalize()+pd.Timedelta(days=7)).dt.strftime("%Y-%m-%d")
c.to_csv(D+"trading/company_flags_monthly.csv",index=False)
print(len(c), c.company.value_counts().to_dict(), c.signal.value_counts().to_dict())
print("site flags:",(f.flag!=0).sum(),"of",f.flag.notna().sum(), "site-months; from images:",((f.flag!=0)&f.r_viirs.isna()).sum())
