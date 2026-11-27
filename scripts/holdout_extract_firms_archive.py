# Holdout data: VIIRS S-NPP detections within 2 km of each site, from the FIRMS yearly archives (viirs-snpp_YYYY_all_countries.zip), 2012-2018.
import sys,zipfile,os,pandas as pd,numpy as np
sys.path.insert(0,"/home/user/OrbitCu---SWE/src"); from orbitcu.sites import load_sites
from orbitcu.geolocate import haversine_km
S=[(s.id,s.country,s.lat,s.lon) for s in load_sites()]+[("PL-LEGNICA","Poland",51.18,16.115)]
CMAP={"China":"China","Japan":"Japan","South Korea":"Republic_of_Korea","India":"India","Indonesia":"Indonesia","Poland":"Poland","Germany":"Germany","Bulgaria":"Bulgaria","Russia":"Russian_Federation","Chile":"Chile","Peru":"Peru","Iran":"Iran","DR Congo":"Democratic_Republic_of_the_Congo"}
out={sid:[] for sid,*_ in S}
for y in range(2012,2019):
    z=zipfile.ZipFile(f"/home/user/data_run/firmsz/viirs-snpp_{y}_all_countries.zip")
    for c in sorted({c for _,c,_,_ in S}):
        name=f"viirs-snpp/{y}/viirs-snpp_{y}_{CMAP[c]}.csv"
        for ch in pd.read_csv(z.open(name),usecols=["latitude","longitude","acq_date","frp"],chunksize=500000):
            for sid,cc,lat,lon in S:
                if cc!=c: continue
                q=ch[(ch.latitude.sub(lat).abs()<0.05)&(ch.longitude.sub(lon).abs()<0.08)]
                q=q[haversine_km(q.latitude.values,q.longitude.values,lat,lon)<=2.0]
                if len(q): out[sid].append(q)
    print(y,flush=True)
os.makedirs("/home/user/data_run/firms/raw",exist_ok=True)
for sid,v in out.items():
    d=pd.concat(v) if v else pd.DataFrame(columns=["latitude","longitude","acq_date","frp"])
    d.to_csv(f"/home/user/data_run/firms/raw/{sid}.csv",index=False); print(sid,len(d))
