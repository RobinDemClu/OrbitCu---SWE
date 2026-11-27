# Holdout data: ERA5 total cloud cover per site, 2011-12 to 2019-01, CDS point time series.
import cdsapi,os,sys,zipfile,glob,io,pandas as pd
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,"/home/user/OrbitCu---SWE/src"); from orbitcu.sites import load_sites
S=[(s.id,s.lat,s.lon) for s in load_sites()]+[("PL-LEGNICA",51.18,16.115)]
def one(x):
    sid,lat,lon=x; out=f"/home/user/data_run/era5ts/out/{sid}_2012_2018.parquet"
    if os.path.exists(out): return sid,"skip"
    for k in range(40):
        try:
            c=cdsapi.Client(url="https://cds.climate.copernicus.eu/api",key=os.environ["CDSAPI_KEY"],quiet=True,progress=False)
            z=f"/home/user/data_run/era5ts/{sid}.zip"
            c.retrieve("reanalysis-era5-single-levels-timeseries",{"variable":["total_cloud_cover"],"location":{"longitude":lon,"latitude":lat},"date":["2011-12-01/2019-01-31"],"data_format":"csv"},z)
            zf=zipfile.ZipFile(z); n=[m for m in zf.namelist() if m.endswith(".csv")][0]
            d=pd.read_csv(zf.open(n)); d[["valid_time","tcc"]].to_parquet(out); os.remove(z); return sid,len(d)
        except Exception as e:
            err=str(e)[:200]; import time; time.sleep(120)
    return sid,"FAIL "+err
with ThreadPoolExecutor(2) as ex:
    for r in ex.map(one,S): print(r,flush=True)
