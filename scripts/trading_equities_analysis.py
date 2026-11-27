"""Operator shares around published output surprises. Abnormal returns (share minus local index: KGHM vs WIG20 TR ETF, Aurubis vs MDAX, Southern Copper vs S&P 500, Yunnan Copper vs SZSE Component, China Daye vs Hang Seng, Adani vs Nifty, Ivanhoe vs TSX) were computed from Yahoo daily closes in a browser and pasted here as entry-to-pre-publication and 2-day publication log returns (bp)."""
import pandas as pd, numpy as np
from scipy.stats import spearmanr
main=",,,,,,,-1340:93,,,-131:420,-131:420,,-385:-160,,-385:-160,485:-213,,,,,,,,84:-141,84:-141,2570:-102,,,,,,,,937:-175,937:-175,,433:180,,,329:249,,329:249,,,,-679:-171,-679:-171,1313:-115,-1426:90,1313:-115,,,243:52,243:52,,,-844:-31,,-844:-31,,-1691:112,-1691:112,1747:850,,1747:850,,-204:532,-204:532,,,-696:-306,1017:-739,-453:-406,1017:-739,-1244:137,,,-1244:137,739:43,,739:43,-252:-123,,-252:-123,-13:391,,,,2751:-189,-1007:-71,2751:-189,,-1007:-71,-1431:-28,3051:184,,855:195,,855:195,,,1941:152,1325:-980,1325:-980,1941:152,5625:-454,NaN:-185,-647:381,,".split(",")
kg=",,90:-85,-425:23,-292:-31,193:172,331:-264,352:-48,925:258,1158:493,-514:130,-148:14,46:-133,747:-407,660:-193,395:-471,-323:46,-296:170,8:-80,-142:-258,-222:43,-484:-299,-377:116,1128:-930,-859:-376,85:392,202:-216,-39:274,-302:372,76:-270,1219:14,664:-462,-392:213,-533:-323".split(",")
import os; os.chdir(os.environ.get("ORBITCU_DATA","/mnt/project-files/data/").rstrip("/")+"/trading")
t=pd.read_csv("equity_cases.csv"); assert len(main)==len(t)
it=iter(kg)
vals=[next(it) if e=="PL-KGHM" else m for e,m in zip(t.entity,main)]
t["ar_lead"]=[float(v.split(":")[0])/1e4 if v else np.nan for v in vals]
t["ar_pub"]=[float(v.split(":")[1])/1e4 if v else np.nan for v in vals]
t["company"]=t.entity.map({"PL-KGHM":"KGHM","DE-HAMBURG":"Aurubis","BG-PIRDOP":"Aurubis","PE-ILO":"Southern Copper","CN-SOUTHEAST":"Yunnan Copper","CN-CHIFENG-YJ":"Yunnan Copper","CN-DAYE":"China Daye","IN-KUTCH":"Adani Ent.","CD-KAMOA":"Ivanhoe"})
t.to_csv("equity_cases_returns.csv",index=False)
# collapse company-period (two plants of one company published the same day): keep sum of signals
g=t.dropna(subset=["ar_pub"]).groupby(["company","published_at"]).agg(dev=("dev","mean"),surprise=("surprise","sum"),flag=("flag_comb","sum"),ar_lead=("ar_lead","first"),ar_pub=("ar_pub","first")).reset_index()
g["surprise"]=np.sign(g.surprise); g["flag"]=np.sign(g.flag)
print("company-periods:",len(g), g.company.value_counts().to_dict())
r=spearmanr(g.dev,g.ar_pub); print("output deviation vs abnormal return at publication (2 days): rho %.2f p %.3f n %d"%(r[0],r[1],len(g)))
s=g[g.surprise!=0]; x=s.surprise*s.ar_pub; print("big surprises: n",len(s),"mean signed AR %.2f%% hit %.2f t %.2f"%(100*x.mean(),(x>0).mean(),x.mean()/x.std()*np.sqrt(len(x))))
f=g[g.flag!=0].copy(); y=(f.flag*f.ar_lead).dropna()-8e-4
print("trade flags until day before publication: n",len(y),"mean %.2f%% hit %.2f t %.2f"%(100*y.mean(),(y>0).mean(),y.mean()/y.std()*np.sqrt(len(y))))
z=(f.flag*(f.ar_lead+f.ar_pub)).dropna()-8e-4
print("trade flags through publication: n",len(z),"mean %.2f%% hit %.2f t %.2f"%(100*z.mean(),(z>0).mean(),z.mean()/z.std()*np.sqrt(len(z))))
print(f[["company","published_at","surprise","flag","ar_lead","ar_pub"]].round(3).to_string())
pd.DataFrame([dict(test="output deviation vs 2-day abnormal return at publication",n=len(g),stat=r[0],p=r[1]),
 dict(test="big surprises: signed 2-day abnormal return",n=len(s),stat=x.mean(),p=np.nan),
 dict(test="trade flags until publication (abnormal return, 8bp)",n=len(y),stat=y.mean(),p=np.nan),
 dict(test="trade flags through publication",n=len(z),stat=z.mean(),p=np.nan)]).to_csv("equity_tests.csv",index=False)
