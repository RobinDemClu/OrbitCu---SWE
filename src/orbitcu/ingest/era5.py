"""ERA5 hourly winds and boundary-layer height from the Copernicus CDS.

Used by the SO2 emission step (FR-3/FR-4): mass-balance / flux methods need
the wind that transported the plume at the TROPOMI overpass time (Fioletov
et al., 2015, 2020). We request, for each site, a small box covering its 50 km
SO2 disc (0.25 deg grid):

* ``10m_u/v_component_of_wind`` and ``100m_u/v_component_of_wind``;
* ``boundary_layer_height``.

Pressure-level winds (e.g. 900 hPa) can be added for plumes above the surface
layer. Requests are monthly per site to stay well under CDS size limits.

Point-in-time: ERA5T (preliminary) is published ~5 days behind real time and
replaced by final ERA5 2-3 months later; we use ``available_at = valid_time +
5 days``. Differences ERA5T vs ERA5 are small and documented by ECMWF.

Authentication: ``CDSAPI_KEY`` env var (and ``CDSAPI_URL``, defaulting to the
new CDS). The ERA5 licence must be accepted once on the CDS web site.
"""

from __future__ import annotations

import calendar
import os
from pathlib import Path

import pandas as pd

from ..sites import Site
from .pit import LATENCY

DATASET = "reanalysis-era5-single-levels"
VARIABLES = [
    "10m_u_component_of_wind", "10m_v_component_of_wind",
    "100m_u_component_of_wind", "100m_v_component_of_wind",
    "boundary_layer_height",
]
CDS_URL = "https://cds.climate.copernicus.eu/api"


def area(site: Site, pad_deg: float = 0.25) -> list[float]:
    """CDS ``area`` = [N, W, S, E] covering the SO2 disc, on the 0.25 deg grid."""
    w, s, e, n = site.so2_bbox()
    snap = lambda v, f: round(f(v / 0.25) * 0.25, 2)  # noqa: E731
    import math
    return [snap(n + pad_deg, math.ceil), snap(w - pad_deg, math.floor),
            snap(s - pad_deg, math.floor), snap(e + pad_deg, math.ceil)]


def request(site: Site, year: int, month: int) -> dict:
    ndays = calendar.monthrange(year, month)[1]
    return {
        "product_type": ["reanalysis"],
        "variable": VARIABLES,
        "year": [str(year)],
        "month": [f"{month:02d}"],
        "day": [f"{d:02d}" for d in range(1, ndays + 1)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": area(site),
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def overpass_hours(site: Site) -> list[int]:
    """UTC hours bracketing the Sentinel-5P overpass (~13:30 local solar time).

    Above 60 deg latitude consecutive orbits overlap, so a site is seen at
    several times of day: all 24 hours are then requested.
    """
    if abs(site.lat) > 60:
        return list(range(24))
    h0 = int((13.5 - site.lon / 15.0) // 1)
    return sorted({(h0 + k) % 24 for k in (-1, 0, 1, 2)})


def request_overpass(site: Site, year: int, months: list[int]) -> dict:
    return {
        "product_type": ["reanalysis"],
        "variable": VARIABLES,
        "year": [str(year)],
        "month": [f"{m:02d}" for m in months],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": [f"{h:02d}:00" for h in overpass_hours(site)],
        "area": area(site),
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def retrieve_overpass(site: Site, year: int, out_dir: Path, last_month: int = 12) -> Path:
    """One request per site and year, overpass hours only (used by the SO2 fit).

    ``last_month`` < 12 for the current year (CDS refuses dates not yet
    produced); the file name then carries the month so a later call with a
    longer range downloads again.
    """
    import cdsapi

    tag = f"{year}" if last_month == 12 else f"{year}-to{last_month:02d}"
    out = out_dir / "era5_overpass" / site.id / f"{tag}.nc"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    c = cdsapi.Client(url=os.environ.get("CDSAPI_URL", CDS_URL), key=os.environ["CDSAPI_KEY"], quiet=True)
    tmp = out.with_suffix(".part")
    c.retrieve(DATASET, request_overpass(site, year, list(range(1, last_month + 1))), str(tmp))
    tmp.rename(out)
    return out


TS_DATASET = "reanalysis-era5-single-levels-timeseries"


def retrieve_timeseries(site: Site, start: str, end: str, out_dir: Path) -> Path:
    """Hourly ERA5 10 m and 100 m wind at the smelter point, as a Parquet table.

    Uses the CDS *ERA5 time series* dataset (cloud-optimised ARCO store): a
    whole multi-year series for one point comes back in ~30 s, whereas
    gridded requests can wait hours in the shared ERA5 queue. The nearest
    0.25 deg grid point is returned. Columns: valid_time (UTC), u100, v100,
    u10, v10, available_at (valid_time + 5 days, ERA5T latency rule).
    """
    import io
    import zipfile

    import cdsapi

    out = out_dir / "era5_ts" / f"{site.id}_{start}_{end}.parquet"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    c = cdsapi.Client(url=os.environ.get("CDSAPI_URL", CDS_URL), key=os.environ["CDSAPI_KEY"], quiet=True)
    tmp = out.with_suffix(".zip")
    c.retrieve(TS_DATASET, {
        "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind",
                     "100m_u_component_of_wind", "100m_v_component_of_wind"],
        "location": {"longitude": round(site.lon, 3), "latitude": round(site.lat, 3)},
        "date": [f"{start}/{end}"],
        "data_format": "csv",
    }, str(tmp))
    with zipfile.ZipFile(tmp) as z:
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        df = pd.read_csv(io.BytesIO(z.read(name)))
    tmp.unlink()
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    df["site_id"] = site.id
    df["available_at"] = df["valid_time"] + LATENCY["era5t"]
    df.to_parquet(out)
    return out


def retrieve(site: Site, year: int, month: int, out_dir: Path) -> Path:
    import cdsapi

    out = out_dir / site.id / "era5" / f"{year}-{month:02d}.nc"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    c = cdsapi.Client(url=os.environ.get("CDSAPI_URL", CDS_URL), key=os.environ["CDSAPI_KEY"], quiet=True)
    c.retrieve(DATASET, request(site, year, month), str(out))
    return out


def to_table(path: Path, site_id: str) -> pd.DataFrame:
    """Flatten a monthly netCDF into a tidy table with PIT columns."""
    import xarray as xr

    ds = xr.open_dataset(path)
    tname = "valid_time" if "valid_time" in ds.coords else "time"
    df = ds.to_dataframe().reset_index().rename(columns={tname: "valid_time"})
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    df["site_id"] = site_id
    df["available_at"] = df["valid_time"] + LATENCY["era5t"]
    df["pit_basis"] = "latency_rule:era5t"
    return df
