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


def search_all(sites: list[Site], start: str, end: str, modes=("OFFL", "RPRO", "NRTI"), cat=None) -> pd.DataFrame:
    """Union of the orbits touching any site; one row per (orbit file, site)."""
    cat = cat or client()
    # Weekly windows: a whole summer month at high latitude (Norilsk sees up
    # to ~14 orbits a day) makes the STAC search exceed the server timeout.
    edges = pd.date_range(start, pd.Timestamp(end) + pd.Timedelta(days=1), freq="7D").append(
        pd.DatetimeIndex([pd.Timestamp(end) + pd.Timedelta(days=1)])).unique()
    wins = [(a.date().isoformat(), (b - pd.Timedelta(seconds=1)).isoformat())
            for a, b in zip(edges[:-1], edges[1:]) if a < b]
    parts = [_with_retry(s5p.search_orbits, s, a, b, modes, cat, tries=6) for s in sites for a, b in wins]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    # One file per (orbit, site): the Planetary Computer archive misses some
    # OFFL orbits (e.g. most of July 2025), so RPRO then NRTI fill the gaps.
    rank = {"OFFL": 0, "RPRO": 1, "NRTI": 2}
    df["_r"] = df["mode"].map(rank).fillna(9)
    key = df["orbit"].fillna(df["acquired_at"].dt.floor("h").astype(str))
    df = df.assign(_k=key).sort_values("_r").drop_duplicates(["_k", "site_id"]).drop(columns=["_r", "_k"])
    return df.sort_values("acquired_at").reset_index(drop=True)


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
            if df["pixel_time_utc"].isna().all():
                # older processor versions leave time_utc empty: use the
                # reference time (s since 2010-01-01) + delta_time (ms)
                t0 = pd.Timestamp("2010-01-01", tz="UTC") + pd.to_timedelta(int(h["PRODUCT/time"][0]), "s")
                dms = h["PRODUCT/delta_time"][0, sl]
                dms = dms[:, 0] if dms.ndim == 2 else dms
                df["pixel_time_utc"] = t0 + pd.to_timedelta(dms[ii].astype("int64"), "ms")
            df = df[df["qa_value"] >= qa_min]
            if not df.empty:
                out.append(df)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _with_retry(fn, *args, tries: int = 4):
    for k in range(tries):
        try:
            return fn(*args)
        except FileNotFoundError:
            # listed in STAC but absent from storage (HTTP 404): permanent
            raise
        except Exception as e:  # network resets through the proxy are common
            if k == tries - 1:
                raise
            log.warning("retry %s after %s", k + 1, type(e).__name__)
            time.sleep(5 * (k + 1))


def ingest_month(sites: list[Site], month: pd.Period, out_dir: Path, workers: int = 8,
                 modes=("OFFL", "RPRO", "NRTI")) -> Path:
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
        parts, failed, missing = [], 0, 0
        for fut in as_completed(jobs):
            meta = jobs[fut]
            try:
                px = fut.result()
            except FileNotFoundError:
                missing += 1
                continue
            except Exception as e:
                log.error("orbit %s failed: %s", meta.item_id, e)
                failed += 1
                continue
            if px.empty:
                continue
            px["item_id"], px["mode"], px["orbit"] = meta.item_id, meta["mode"], meta.orbit
            px["acquired_at"], px["available_at"] = meta.acquired_at, meta.available_at
            parts.append(px)
    if failed:
        # never cache an incomplete month: it would be skipped on the next run
        raise RuntimeError(f"{month}: {failed}/{len(jobs)} orbits failed; month not saved, rerun to retry")
    if missing:
        log.warning("%s: %d/%d orbit files listed but missing from storage (404), skipped",
                    month, missing, len(jobs))
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp)
    tmp.rename(path)
    return path


# Pixel time minus item datetime (minutes) as a cubic in latitude, fitted on
# 2025 files (|residual| < 8 min for 95 % of pixels). Used for cached files
# from older processor versions whose time_utc field is empty.
_DT_POLY = (-4.43377342e-05, 1.07067466e-03, 3.12000736e-01, 1.91809459e-02)


def fill_pixel_time(df: pd.DataFrame) -> pd.DataFrame:
    df["pixel_time_utc"] = pd.to_datetime(df["pixel_time_utc"], utc=True).astype("datetime64[ns, UTC]")
    miss = df["pixel_time_utc"].isna()
    if miss.any():
        mins = np.polyval(_DT_POLY, df.loc[miss, "lat"].to_numpy())
        df.loc[miss, "pixel_time_utc"] = (df.loc[miss, "acquired_at"].astype("datetime64[ns, UTC]")
                                          + pd.to_timedelta(mins, "min"))
    return df


def load(out_dir: Path, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    files = sorted((out_dir / "s5p_batch").glob("*.parquet"))
    if start:
        files = [f for f in files if f.stem >= start[:7]]
    if end:
        files = [f for f in files if f.stem <= end[:7]]
    parts = [pd.read_parquet(f) for f in files]
    parts = [fill_pixel_time(p) for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
