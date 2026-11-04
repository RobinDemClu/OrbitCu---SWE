"""Sentinel-5P TROPOMI L2 SO2 ingestion (Planetary Computer ``sentinel-5p-l2-netcdf``).

TROPOMI files hold one full orbit (~450 x 4000 ground pixels, 5.5 x 3.5 km at
nadir). We never download a whole file: the netCDF4/HDF5 file is opened over
HTTP with byte-range reads (``fsspec`` + ``h5netcdf``), the geolocation arrays
are read once, and only the scanlines that intersect the site's 50 km SO2 disc
are read for the data variables.

Point-in-time: the processing time is encoded in the file name
(``..._<orbit>_<collection>_<processor>_<YYYYMMDDTHHMMSS>.nc``) and is used
as ``available_at``. NRTI products are preferred for live use; OFFL products
(better quality, released later) are what a backfill normally uses - keeping
the real processing time means a backtest only sees OFFL values once they
existed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..sites import Site
from .optical import client
from .pit import available_at

COLLECTION = "sentinel-5p-l2-netcdf"
PRODUCT_TYPE = "L2__SO2___"
SO2_VAR = "sulfurdioxide_total_vertical_column"  # mol m-2
QA_MIN = 0.5  # S5P SO2 PUM recommendation for most uses
_PROC_RE = re.compile(r"_(\d{8}T\d{6})\.nc")


def processing_time(href: str) -> datetime | None:
    m = _PROC_RE.search(href.split("?")[0])
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def search_orbits(site: Site, start: str, end: str, modes=("OFFL", "NRTI"), cat=None) -> pd.DataFrame:
    cat = cat or client()
    s = cat.search(collections=[COLLECTION], intersects=site.so2_polygon().__geo_interface__,
                   datetime=f"{start}/{end}",
                   query={"s5p:product_type": {"eq": PRODUCT_TYPE}, "s5p:processing_mode": {"in": list(modes)}})
    rows = []
    for it in s.items():
        href = it.assets["so2"].href
        mode = it.properties["s5p:processing_mode"]
        av, basis = available_at(it.properties["datetime"], "s5p_nrti" if mode == "NRTI" else "s5p_offl",
                                 processing_time(href))
        rows.append({
            "site_id": site.id, "item_id": it.id, "mode": mode,
            "orbit": it.properties.get("sat:absolute_orbit"),
            "acquired_at": pd.Timestamp(it.properties["datetime"]).tz_convert("UTC"),
            "available_at": pd.Timestamp(av), "pit_basis": basis, "href": href,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Same orbit can exist as NRTI and OFFL: keep both (different available_at).
    return df.sort_values(["acquired_at", "mode"]).reset_index(drop=True)


def _rows_in_disc(lat: np.ndarray, lon: np.ndarray, site: Site) -> tuple[slice, np.ndarray]:
    from ..geolocate import haversine_km

    d = haversine_km(lat, lon, site.lat, site.lon)
    inside = d <= site.so2_radius_km
    rows = np.where(inside.any(axis=1))[0]
    if rows.size == 0:
        return slice(0, 0), inside
    sl = slice(int(rows.min()), int(rows.max()) + 1)
    return sl, inside[sl]


def extract_orbit(site: Site, href: str) -> pd.DataFrame:
    """Ground pixels of one orbit inside the site's SO2 disc (range reads only)."""
    import fsspec
    import xarray as xr

    with fsspec.open(href, "rb", block_size=2 ** 20) as f:
        ds = xr.open_dataset(f, group="PRODUCT", engine="h5netcdf", decode_times=False)
        lat = ds["latitude"].isel(time=0).values
        lon = ds["longitude"].isel(time=0).values
        sl, inside = _rows_in_disc(lat, lon, site)
        if inside.size == 0 or not inside.any():
            return pd.DataFrame()
        sub = ds.isel(time=0, scanline=sl)
        so2 = sub[SO2_VAR].values
        qa = sub["qa_value"].values
        t = sub["time_utc"].values
    ii, jj = np.where(inside)
    return pd.DataFrame({
        "site_id": site.id,
        "lat": lat[sl][ii, jj], "lon": lon[sl][ii, jj],
        "so2_mol_m2": so2[ii, jj], "qa_value": qa[ii, jj],
        "pixel_time_utc": pd.to_datetime(t[ii].astype(str), utc=True, errors="coerce"),
    })
