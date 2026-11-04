"""Sentinel-2 L2A and Landsat 8/9 Collection-2 L2 ingestion via Planetary Computer.

Scene discovery uses the STAC API; pixels are fetched with the Planetary
Computer *data API* (TiTiler) ``/item/bbox`` endpoint, which crops the
Cloud-Optimised GeoTIFFs server side and returns only the site's thermal AOI
in the scene's native UTM grid. Nothing outside the AOI is downloaded (a
3 x 3 km box at 20 m is ~90 kB per scene instead of ~1 GB per tile).

For each (site, scene) we keep:

* a multi-band GeoTIFF clip (SWIR + NIR + quality band);
* a manifest row with ``acquired_at``, the scene-level cloud cover, the
  AOI-level cloud / shadow fraction computed from the quality band, and
  ``available_at`` from the provider's processing timestamp.
"""

from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pystac_client
import planetary_computer
import rasterio
import requests

from ..sites import Site
from .pit import available_at

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
DATA_API = "https://planetarycomputer.microsoft.com/api/data/v1"


@dataclass(frozen=True)
class Sensor:
    key: str
    collection: str
    assets: tuple[str, ...]  # last one is the quality band
    res_m: int
    pit_rule: str
    provider_ts_field: str
    query: dict | None = None


SENTINEL2 = Sensor(
    key="sentinel2",
    collection="sentinel-2-l2a",
    assets=("B8A", "B11", "B12", "SCL"),
    res_m=20,
    pit_rule="sentinel2_l2a",
    provider_ts_field="s2:generation_time",
)
LANDSAT = Sensor(
    key="landsat",
    collection="landsat-c2-l2",
    assets=("nir08", "swir16", "swir22", "lwir11", "qa_pixel"),
    res_m=30,
    pit_rule="landsat_c2_l2",
    provider_ts_field="created",
    query={"platform": {"in": ["landsat-8", "landsat-9"]}},
)
SENSORS = {s.key: s for s in (SENTINEL2, LANDSAT)}

# Sentinel-2 Scene Classification: 3 cloud shadow, 8/9 cloud medium/high, 10 thin cirrus
S2_CLOUD = {8, 9, 10}
S2_SHADOW = {3}
S2_NODATA = {0}
# Landsat QA_PIXEL bits: 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow
LS_FILL, LS_DILATED, LS_CIRRUS, LS_CLOUD, LS_SHADOW = 0, 1, 2, 3, 4


def client() -> pystac_client.Client:
    return pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)


def search_scenes(site: Site, sensor: Sensor, start: str, end: str, max_cloud: float | None = None,
                  cat: pystac_client.Client | None = None, cover_only: bool = True) -> pd.DataFrame:
    """List scenes intersecting the site's thermal AOI with PIT timestamps."""
    cat = cat or client()
    query = dict(sensor.query or {})
    if max_cloud is not None:
        query["eo:cloud_cover"] = {"lte": max_cloud}
    search = cat.search(collections=[sensor.collection], bbox=site.thermal_bbox(),
                        datetime=f"{start}/{end}", query=query or None)
    from shapely.geometry import box as _box, shape

    aoi = _box(*site.thermal_bbox())
    rows = []
    for it in search.items():
        p = it.properties
        # Tile edges: STAC footprints overlap the AOI but may only partly cover it.
        covers = shape(it.geometry).contains(aoi)
        av, basis = available_at(p["datetime"], sensor.pit_rule, p.get(sensor.provider_ts_field))
        rows.append({
            "site_id": site.id,
            "sensor": sensor.key,
            "item_id": it.id,
            "platform": p.get("platform"),
            "acquired_at": pd.Timestamp(p["datetime"]).tz_convert("UTC"),
            "available_at": pd.Timestamp(av),
            "pit_basis": f"{basis}:{sensor.provider_ts_field}" if basis == "provider_timestamp" else f"{basis}:{sensor.pit_rule}",
            "scene_cloud_pct": p.get("eo:cloud_cover"),
            "proj_code": p.get("proj:code") or (f"EPSG:{p['proj:epsg']}" if p.get("proj:epsg") else None),
            "processing_baseline": p.get("s2:processing_baseline") or p.get("landsat:collection_category"),
            "covers_aoi": covers,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Sentinel-2 MGRS tiles and Landsat WRS-2 rows overlap: the same acquisition
    # can appear in two items (seconds apart). Keep the tile that fully covers the AOI (or the first one).
    df = df.sort_values(["acquired_at", "covers_aoi"], ascending=[True, False])
    df["acq_key"] = df["acquired_at"].dt.round("5min").astype(str) + "_" + df["platform"].astype(str)
    df = df.drop_duplicates("acq_key").drop(columns="acq_key")
    if cover_only:
        df = df[df["covers_aoi"]]
    return df.reset_index(drop=True)


def aoi_window(site: Site, epsg: str, res_m: int) -> tuple[float, float, float, float]:
    """Thermal AOI in the scene CRS, snapped outward to the pixel grid."""
    from pyproj import Transformer
    from shapely.geometry import box
    from shapely.ops import transform

    t = Transformer.from_crs(site.crs, epsg, always_xy=True)
    minx, miny, maxx, maxy = transform(t.transform, box(*site.thermal_box_utm())).bounds
    snap = lambda v, f: f(v / res_m) * res_m  # noqa: E731
    return (snap(minx, np.floor), snap(miny, np.floor), snap(maxx, np.ceil), snap(maxy, np.ceil))


def fetch_clip(site: Site, sensor: Sensor, item_id: str, epsg: str, retries: int = 4) -> bytes:
    """Return the AOI clip as GeoTIFF bytes (bands in ``sensor.assets`` order)."""
    minx, miny, maxx, maxy = aoi_window(site, epsg, sensor.res_m)
    w = int(round((maxx - minx) / sensor.res_m))
    h = int(round((maxy - miny) / sensor.res_m))
    params = [("collection", sensor.collection), ("item", item_id), ("coord_crs", epsg),
              ("dst_crs", epsg), ("return_mask", "false"), ("resampling", "nearest")]
    params += [("assets", a) for a in sensor.assets]
    url = f"{DATA_API}/item/bbox/{minx},{miny},{maxx},{maxy}/{w}x{h}.tif"
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=120)
        except requests.ConnectionError:
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code == 200:
            return r.content
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"data API clip failed after {retries} attempts for {item_id}")


def quality_fractions(sensor: Sensor, qa: np.ndarray) -> dict:
    """AOI-level cloud, shadow and no-data fractions from the quality band."""
    qa = qa.astype(np.int64)
    if sensor.key == "sentinel2":
        nodata = np.isin(qa, list(S2_NODATA))
        cloud = np.isin(qa, list(S2_CLOUD))
        shadow = np.isin(qa, list(S2_SHADOW))
    else:
        bit = lambda b: ((qa >> b) & 1).astype(bool)  # noqa: E731
        nodata = bit(LS_FILL) | (qa == 0)
        cloud = bit(LS_CLOUD) | bit(LS_DILATED) | bit(LS_CIRRUS)
        shadow = bit(LS_SHADOW)
    valid = ~nodata
    n = max(int(valid.sum()), 1)
    return {
        "aoi_nodata_frac": round(float(nodata.mean()), 4),
        "aoi_cloud_frac": round(float((cloud & valid).sum() / n), 4),
        "aoi_shadow_frac": round(float((shadow & valid).sum() / n), 4),
    }


def ingest_site(site: Site, sensor: Sensor, start: str, end: str, out_dir: Path,
                max_cloud: float | None = None, cat=None, workers: int = 8) -> pd.DataFrame:
    """Search, clip and store all scenes for one site; return the manifest."""
    scenes = search_scenes(site, sensor, start, end, max_cloud=max_cloud, cat=cat)
    if scenes.empty:
        return scenes
    clip_dir = out_dir / site.id / sensor.key
    clip_dir.mkdir(parents=True, exist_ok=True)

    def one(row) -> dict:
        path = clip_dir / f"{row.item_id}.tif"
        rec = {"clip_path": str(path.relative_to(out_dir)), "error": None}
        try:
            if not path.exists():
                path.write_bytes(fetch_clip(site, sensor, row.item_id, row.proj_code))
            with rasterio.open(path) as src:
                arr = src.read()
                rec.update(quality_fractions(sensor, arr[-1]))
                rec["swir_max"] = int(arr[-2 if sensor.key == "sentinel2" else -3].max())
        except Exception as exc:  # keep going; the manifest records the failure
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        rec["ingested_at"] = pd.Timestamp(datetime.now(timezone.utc))
        return rec

    with ThreadPoolExecutor(workers) as ex:
        extra = list(ex.map(one, scenes.itertuples(index=False)))
    return pd.concat([scenes, pd.DataFrame(extra)], axis=1)


def read_clip(data: bytes | str | Path) -> tuple[np.ndarray, dict]:
    src = rasterio.open(io.BytesIO(data)) if isinstance(data, bytes) else rasterio.open(data)
    with src:
        return src.read(), src.profile
