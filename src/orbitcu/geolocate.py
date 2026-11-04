"""Refine smelter coordinates from persistent VIIRS thermal anomalies.

A primary copper smelter (flash furnace, converters, anode furnaces, acid
plant) is a *persistent* industrial heat source: VIIRS 375 m detects it on a
large share of cloud-free night overpasses, as for steel plants and gas flares
(Liu et al., 2018; Elvidge et al., 2013). Given a town-level prior we:

1. download one year of VIIRS detections in a +/- ``search_deg`` box;
2. greedily extract clusters: densest 500 m grid cell -> all detections within
   ``cluster_km`` -> median position -> remove -> repeat;
3. report the top clusters with their detection count, number of distinct
   detection days, share of FIRMS ``type == 2`` ("static land source") and
   distance to the prior.

The analyst then picks the cluster that matches the smelter (checked against
Sentinel-2 imagery) - the choice and evidence are written to
``data/registry/smelter_sites.csv``. This module never overwrites a manual
choice.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

EARTH_KM_PER_DEG = 111.32


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def search_bbox(lat: float, lon: float, search_deg: float = 0.25):
    dlon = search_deg / max(math.cos(math.radians(lat)), 0.2)
    return (round(lon - dlon, 4), round(lat - search_deg, 4), round(lon + dlon, 4), round(lat + search_deg, 4))


def clusters(df: pd.DataFrame, prior_lat: float, prior_lon: float, cluster_km: float = 1.5,
             cell_deg: float = 0.005, max_clusters: int = 5, min_count: int = 5) -> pd.DataFrame:
    """Greedy density clustering of FIRMS detections (see module doc)."""
    rows = []
    if df.empty:
        return pd.DataFrame(rows)
    pts = df[["latitude", "longitude", "acq_date", "type"]].copy()
    while len(pts) >= min_count and len(rows) < max_clusters:
        cell = (pts["latitude"] / cell_deg).round().astype(int).astype(str) + "_" + \
               (pts["longitude"] / cell_deg).round().astype(int).astype(str)
        top = cell.value_counts().index[0]
        seed = pts[cell == top]
        clat, clon = seed["latitude"].median(), seed["longitude"].median()
        d = haversine_km(pts["latitude"].values, pts["longitude"].values, clat, clon)
        member = pts[d <= cluster_km]
        if len(member) < min_count:
            break
        mlat, mlon = member["latitude"].median(), member["longitude"].median()
        t = pd.to_numeric(member["type"], errors="coerce")
        rows.append({
            "lat": round(float(mlat), 5),
            "lon": round(float(mlon), 5),
            "n_detections": int(len(member)),
            "n_days": int(member["acq_date"].nunique()),
            "share_static": round(float((t == 2).mean()), 3) if t.notna().any() else None,
            "spread_km": round(float(np.median(haversine_km(member["latitude"].values, member["longitude"].values, mlat, mlon))), 3),
            "dist_prior_km": round(float(haversine_km(mlat, mlon, prior_lat, prior_lon)), 2),
        })
        pts = pts[d > cluster_km]
    return pd.DataFrame(rows)


# --- Sentinel-2 SWIR confirmation -------------------------------------------------
# Thermal Anomaly Index (Marchese et al., 2019): TAI = (B12 - B11) / B8A. Pixels of
# open molten metal / slag and hot furnace hoods have TAI > 0.45 with a large
# B12 - B11 difference. A smelter shows such pixels on a large share of clear
# scenes, which confirms both the location and the activity of the plant.
TAI_MIN = 0.45
TAI_DIFF_MIN = 150     # B12 - B11, reflectance x 1e4
TAI_B12_MIN = 1500


def hot_pixels(b8a, b11, b12) -> "np.ndarray":
    b8a, b11, b12 = (np.asarray(x, dtype=float) for x in (b8a, b11, b12))
    tai = (b12 - b11) / np.maximum(b8a, 1.0)
    return (tai > TAI_MIN) & (b12 - b11 > TAI_DIFF_MIN) & (b12 > TAI_B12_MIN)


def s2_hot_pixel_persistence(lat: float, lon: float, half_km: float, start: str, end: str,
                             n_scenes: int = 20, max_aoi_cloud: float = 0.1) -> dict:
    """Stack the clearest Sentinel-2 scenes over a box and count TAI hot pixels.

    Returns the share of clear scenes with >= 1 hot pixel and the centroid of
    the pixels hot on >= 25 % of those scenes (the furnace area)."""
    import rasterio
    from io import BytesIO
    from pyproj import Transformer

    from .ingest import optical
    from .sites import Site

    site = Site("probe", "probe", "", lat, lon, thermal_half_km=half_km)
    sc = optical.search_scenes(site, optical.SENTINEL2, start, end, max_cloud=30)
    if sc.empty:
        return {"n_clear": 0}
    sc = sc[sc["proj_code"] == sc["proj_code"].mode()[0]].sort_values("scene_cloud_pct").head(n_scenes * 2)
    hits, n, active = None, 0, 0
    for row in sc.itertuples(index=False):
        if n >= n_scenes:
            break
        with rasterio.open(BytesIO(optical.fetch_clip(site, optical.SENTINEL2, row.item_id, row.proj_code))) as src:
            b8a, b11, b12, scl = src.read()
            transform_, crs = src.transform, src.crs
        q = optical.quality_fractions(optical.SENTINEL2, scl)
        if q["aoi_cloud_frac"] > max_aoi_cloud or q["aoi_nodata_frac"] > 0.05:
            continue
        hot = hot_pixels(b8a, b11, b12)
        hits = hot.astype(int) if hits is None else hits + hot
        n += 1
        active += bool(hot.any())
    out = {"n_clear": n, "share_scenes_hot": round(active / n, 3) if n else None}
    if n and hits is not None and (hits >= max(2, 0.25 * n)).any():
        rr, cc = np.where(hits >= max(2, 0.25 * n))
        xs, ys = rasterio.transform.xy(transform_, rr, cc)
        lo, la = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(np.median(xs), np.median(ys))
        out.update({"hot_lat": round(float(la), 5), "hot_lon": round(float(lo), 5),
                    "n_persistent_px": int(len(rr)), "max_hits": int(hits.max())})
    return out
