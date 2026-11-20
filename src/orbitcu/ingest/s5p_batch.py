"""Multi-site, multi-month Sentinel-5P SO2 extraction (one read per orbit).

:mod:`orbitcu.ingest.s5p` opens one orbit file per *site*. For a multi-year
backfill over 29 smelters that is far too slow (~7 s per orbit and site), so
this module groups the work by *orbit*: each TROPOMI file is opened once
(HTTP byte-range reads through ``fsspec`` + ``h5py``), its geolocation is read
once, and the pixels of every site whose 50 km disc falls in the swath are
extracted together.

Per pixel we keep (all from the L2 ``PRODUCT`` group unless noted):

* ``so2_1km``      - ``..._total_vertical_column_1km`` (1 km box profile,
  DETAILED_RESULTS). This is the column used by the emission fit: it
  represents SO2 in the boundary layer, as for a smelter stack plume
  (Fioletov et al., 2020 use a boundary-layer profile for anthropogenic
  point sources);
* ``qa_value``: the QA value already folds in solar zenith angle, cloud
  and snow screening (pixels with QA < 0.5 are dropped).

Only these two variables (plus geolocation) are read because every extra
variable costs whole compressed HDF5 chunks (2087 x 225 pixels) over HTTP.

Results are cached as one Parquet file per (month) under
``<out>/s5p_batch/YYYY-MM.parquet`` with an orbit log so that a run can be
resumed. Point-in-time: ``available_at`` = processing time from the file
name (provider timestamp), as in :mod:`orbitcu.ingest.s5p`.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from ..geolocate import haversine_km
from ..sites import Site
from . import s5p
from .optical import client

log = logging.getLogger(__name__)

VARS = {
    "so2_1km": "PRODUCT/SUPPORT_DATA/DETAILED_RESULTS/sulfurdioxide_total_vertical_column_1km",
    "qa_value": "PRODUCT/qa_value",
}


def search_all(sites: list[Site], start: str, end: str, modes=("OFFL",), cat=None) -> pd.DataFrame:
    """Union of the orbits touching any site; one row per (orbit file, site)."""
    cat = cat or client()
    parts = [s5p.search_orbits(s, start, end, modes=modes, cat=cat) for s in sites]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _scale(ds, arr):
    fv = ds.attrs.get("_FillValue")
    arr = arr.astype("float64")
    if fv is not None:
        arr[arr == fv] = np.nan
    sf = ds.attrs.get("scale_factor")
    if sf is not None:
        arr = arr * float(np.atleast_1d(sf)[0]) + float(np.atleast_1d(ds.attrs.get("add_offset", 0.0))[0])
    return arr


def extract_orbit_multi(href: str, sites: list[Site], qa_min: float = 0.5) -> pd.DataFrame:
    """Pixels of one orbit inside the SO2 disc of each of ``sites``."""
    import fsspec
    import h5py

    out = []
    with fsspec.open(href, "rb", block_size=2 ** 20, cache_type="blockcache",
                     client_kwargs={"trust_env": True}) as f:
        # large chunk cache: chunks (2087 x 225) are shared by neighbouring sites
        h = h5py.File(f, "r", rdcc_nbytes=96 * 2 ** 20, rdcc_nslots=10007)
        lat = h["PRODUCT/latitude"][0]
        lon = h["PRODUCT/longitude"][0]
        tutc = h["PRODUCT/time_utc"][0]
        for site in sites:
            # cheap pre-filter on a lat/lon box before the haversine
            dlat = site.so2_radius_km / 111.0 + 0.1
            dlon = dlat / max(np.cos(np.radians(site.lat)), 0.2)
            box = (np.abs(lat - site.lat) <= dlat) & (np.abs(((lon - site.lon + 180) % 360) - 180) <= dlon)
            rows = np.where(box.any(axis=1))[0]
            if rows.size == 0:
                continue
            sl = slice(int(rows.min()), int(rows.max()) + 1)
            la, lo = lat[sl], lon[sl]
            inside = haversine_km(la, lo, site.lat, site.lon) <= site.so2_radius_km
            if not inside.any():
                continue
            ii, jj = np.where(inside)
            cols = {k: _scale(h[p], h[p][0, sl, :])[ii, jj] for k, p in VARS.items()}
            df = pd.DataFrame({"site_id": site.id, "lat": la[ii, jj], "lon": lo[ii, jj], **cols})
            t = np.array([x.decode() if isinstance(x, bytes) else str(x) for x in tutc[sl][ii]])
            df["pixel_time_utc"] = pd.to_datetime(t, utc=True, errors="coerce", format="ISO8601")
            df = df[df["qa_value"] >= qa_min]
            if not df.empty:
                out.append(df)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _with_retry(fn, *args, tries: int = 4):
    for k in range(tries):
        try:
            return fn(*args)
        except Exception as e:  # network resets through the proxy are common
            if k == tries - 1:
                raise
            log.warning("retry %s after %s", k + 1, type(e).__name__)
            time.sleep(5 * (k + 1))


def ingest_month(sites: list[Site], month: pd.Period, out_dir: Path, workers: int = 8,
                 modes=("OFFL",)) -> Path:
    """Extract one calendar month for all sites; resumable via the cached file."""
    path = out_dir / "s5p_batch" / f"{month}.parquet"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    start, end = month.start_time.date().isoformat(), month.end_time.date().isoformat()
    orbits = search_all(sites, start, end, modes=modes)
    if orbits.empty:
        pd.DataFrame().to_parquet(path)
        return path
    by_item = orbits.groupby("item_id")
    site_map = {s.id: s for s in sites}
    jobs = {}
    # processes, not threads: h5py serialises every call behind one global lock,
    # so threads would read the orbits one after the other
    with ProcessPoolExecutor(workers) as ex:
        for item, g in by_item:
            first = g.iloc[0]
            ss = [site_map[i] for i in g["site_id"].unique()]
            jobs[ex.submit(_with_retry, extract_orbit_multi, first.href, ss)] = first
        parts = []
        for fut in as_completed(jobs):
            meta = jobs[fut]
            try:
                px = fut.result()
            except Exception as e:
                log.error("orbit %s failed: %s", meta.item_id, e)
                continue
            if px.empty:
                continue
            px["item_id"], px["mode"], px["orbit"] = meta.item_id, meta["mode"], meta.orbit
            px["acquired_at"], px["available_at"] = meta.acquired_at, meta.available_at
            parts.append(px)
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp)
    tmp.rename(path)
    return path


def load(out_dir: Path, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    files = sorted((out_dir / "s5p_batch").glob("*.parquet"))
    if start:
        files = [f for f in files if f.stem >= start[:7]]
    if end:
        files = [f for f in files if f.stem <= end[:7]]
    parts = [pd.read_parquet(f) for f in files]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
