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
