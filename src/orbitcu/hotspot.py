"""Per-image heat score with a quality flag (Sentinel-2 L2A and Landsat 8/9 C2 L2).

VIIRS (375 m, night) tells whether a site is hot on a given night. The 20-30 m SWIR images
of Sentinel-2 and Landsat see the furnace hall itself, a few times a week. For every clip of
a site's 3 x 3 km thermal box this module returns:

* a heat score: number of hot pixels, hot area, the summed SWIR excess
  (B12 - B11 over hot pixels, a proxy of the radiated power), the maximum TAI, saturated
  pixels; for Landsat also the surface-temperature excess of the thermal band;
  Hot pixels: candidates by the Thermal Anomaly Index rule (Marchese et al. 2019, the
  same rule as ``geolocate.hot_pixels``), confirmed when TAI > 3 and B12 - B11 > 0.40
  (strict rule tuned on control boxes, see HOT_TAI_MIN);
* a quality flag that says whether a low score can be trusted:

  ``clear``    cloud + shadow < 10 % and no-data < 5 %: score usable, a zero means "cold";
  ``partial``  cloud + shadow < 50 %: hot pixels are real, a zero is weak evidence;
  ``cloudy``   more than half of the box hidden: only a positive score counts;
  ``nodata``   more than half of the box outside the image (swath edge, fill).

  A hot pixel seen through thin cloud is still a hot pixel, so ``hot_seen`` is valid for any
  flag; ``cold_seen`` (zero hot pixels) is only claimed on clear images.

Reflectances are put on the same scale (x 1e4) for both sensors before the rule is applied:
Sentinel-2 L2A products of processing baseline 04.00 and later (25 Jan 2022 onward) carry a
+1000 offset that is removed; Landsat Collection-2 surface reflectance is DN x 2.75e-5 - 0.2.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .geolocate import TAI_B12_MIN, TAI_DIFF_MIN, TAI_MIN

# Sentinel-2 Scene Classification classes
S2_SATURATED = {1}
S2_CLOUD = {8, 9, 10}
S2_SHADOW = {3}
S2_NODATA = {0}
# Landsat QA_PIXEL bits
LS_FILL, LS_DILATED, LS_CIRRUS, LS_CLOUD, LS_SHADOW = 0, 1, 2, 3, 4
LS_SAT_DN = 65000           # surface reflectance DN at or above this = saturated / clipped
LS_ST_SCALE, LS_ST_OFFSET = 0.00341802, 149.0   # Collection-2 surface temperature (K)

CLEAR_MAX, PARTIAL_MAX, NODATA_MAX = 0.10, 0.50, 0.05
# Confirmed hot pixel: much stricter than the TAI candidate rule. On control boxes with no
# hot industry (central Paris, Hamburg container port, Atacama desert, Katanga bush) the
# candidate rule fires on 0-95 % of clear Sentinel-2 images (metal roofs, edges); the strict
# rule on 0-11 %, while it keeps 78-100 % of clear images at Glogow and Hamburg.
HOT_TAI_MIN = 3.0
HOT_DIFF_MIN = 4000         # B12 - B11, reflectance x 1e4


def s2_offset(processing_baseline) -> int:
    """BOA offset (DN) of a Sentinel-2 L2A product: 1000 from baseline 04.00 on."""
    try:
        return 1000 if float(str(processing_baseline)) >= 4.0 else 0
    except (TypeError, ValueError):
        return 0


def to_reflectance(sensor: str, arr: np.ndarray, processing_baseline=None) -> dict:
    """Bands of a clip as reflectance x 1e4 (float) plus the quality band."""
    arr = arr.astype(float)
    if sensor == "sentinel2":
        off = s2_offset(processing_baseline)
        b8a, b11, b12, qa = arr[0] - off, arr[1] - off, arr[2] - off, arr[3]
        raw_swir = arr[2]
        return {"nir": b8a, "swir1": b11, "swir2": b12, "qa": qa, "raw_swir2": raw_swir, "lwir": None}
    nir, s1, s2, lw, qa = arr[0], arr[1], arr[2], arr[3], arr[4]
    sc = lambda x: np.where(x > 0, x * 0.275 - 2000.0, np.nan)  # noqa: E731
    return {"nir": sc(nir), "swir1": sc(s1), "swir2": sc(s2), "qa": qa, "raw_swir2": s2,
            "lwir": np.where(lw > 0, lw * LS_ST_SCALE + LS_ST_OFFSET, np.nan)}


def masks(sensor: str, qa: np.ndarray) -> dict:
    qa = np.nan_to_num(qa).astype(np.int64)
    if sensor == "sentinel2":
        nodata = np.isin(qa, list(S2_NODATA))
        cloud = np.isin(qa, list(S2_CLOUD))
        shadow = np.isin(qa, list(S2_SHADOW))
        sat = np.isin(qa, list(S2_SATURATED))
    else:
        bit = lambda b: ((qa >> b) & 1).astype(bool)  # noqa: E731
        nodata = bit(LS_FILL) | (qa == 0)
        cloud = bit(LS_CLOUD) | bit(LS_DILATED) | bit(LS_CIRRUS)
        shadow = bit(LS_SHADOW)
        sat = np.zeros_like(nodata)
    return {"nodata": nodata, "cloud": cloud & ~nodata, "shadow": shadow & ~nodata, "saturated": sat}


def quality_flag(nodata_frac: float, hidden_frac: float) -> str:
    if nodata_frac > 0.5:
        return "nodata"
    if hidden_frac < CLEAR_MAX and nodata_frac < NODATA_MAX:
        return "clear"
    if hidden_frac < PARTIAL_MAX:
        return "partial"
    return "cloudy"


def score_image(sensor: str, arr: np.ndarray, processing_baseline=None, res_m: float | None = None) -> dict:
    """Heat score and quality flag of one clip (array in ``optical.Sensor.assets`` order)."""
    res_m = res_m or (20.0 if sensor == "sentinel2" else 30.0)
    b = to_reflectance(sensor, arr, processing_baseline)
    m = masks(sensor, b["qa"])
    valid = ~m["nodata"]
    n_valid = int(valid.sum())
    nodata_frac = float(m["nodata"].mean())
    hidden = (m["cloud"] | m["shadow"]) & valid
    hidden_frac = float(hidden.sum() / max(n_valid, 1))

    nir, s1, s2 = (np.nan_to_num(b[k], nan=0.0) for k in ("nir", "swir1", "swir2"))
    tai = (s2 - s1) / np.maximum(nir, 1.0)
    cand = (tai > TAI_MIN) & (s2 - s1 > TAI_DIFF_MIN) & (s2 > TAI_B12_MIN) & valid
    hot = cand & (tai > HOT_TAI_MIN) & (s2 - s1 > HOT_DIFF_MIN)
    if sensor == "sentinel2":
        sat = m["saturated"] & valid
    else:
        sat = (b["raw_swir2"] >= LS_SAT_DN) & valid
    # a saturated SWIR pixel counts as hot when the image has a confirmed hot pixel
    hot_all = hot | (sat & hot.any())
    n_hot = int(hot_all.sum())
    excess = float(np.clip(s2 - s1, 0, None)[hot_all].sum()) / 1e4
    flag = quality_flag(nodata_frac, hidden_frac)
    out = {
        "n_valid_px": n_valid,
        "nodata_frac": round(nodata_frac, 4),
        "cloud_frac": round(float(m["cloud"].sum() / max(n_valid, 1)), 4),
        "shadow_frac": round(float(m["shadow"].sum() / max(n_valid, 1)), 4),
        "quality": flag,
        "n_hot": n_hot,
        "n_candidate": int(cand.sum()),
        "n_saturated": int(sat.sum()),
        "hot_area_m2": n_hot * res_m * res_m,
        "swir_excess": round(excess, 4),
        "tai_max": round(float(tai[valid].max()) if n_valid else np.nan, 3),
        "swir2_max": round(float(s2[valid].max()) if n_valid else np.nan, 1),
        "hot_seen": n_hot > 0,
        "cold_seen": n_hot == 0 and flag == "clear",
    }
    if b["lwir"] is not None:
        lw = np.where(valid & ~hidden, b["lwir"], np.nan)
        if np.isfinite(lw).sum() >= 10:
            out["lst_max_k"] = round(float(np.nanmax(lw)), 2)
            out["lst_excess_k"] = round(float(np.nanmax(lw) - np.nanmedian(lw)), 2)
    return out


def score_manifest(manifest: pd.DataFrame, clip_root: Path | str) -> pd.DataFrame:
    """Score every clip listed in an ingestion manifest (``optical.ingest_site`` output).

    Keeps the point-in-time columns (acquired_at, available_at) so the scores can be used
    as of any date."""
    import rasterio

    clip_root = Path(clip_root)
    rows = []
    keep = ["site_id", "sensor", "item_id", "platform", "acquired_at", "available_at",
            "scene_cloud_pct", "processing_baseline"]
    for r in manifest.itertuples(index=False):
        rec = {k: getattr(r, k, None) for k in keep}
        p = clip_root / str(r.clip_path)
        if not isinstance(r.clip_path, str) or not p.exists():
            rec["quality"] = "missing"
            rows.append(rec)
            continue
        with rasterio.open(p) as src:
            arr = src.read()
            res = abs(src.transform.a)
        rec.update(score_image(r.sensor, arr, r.processing_baseline, res))
        rows.append(rec)
    return pd.DataFrame(rows)


def site_image_summary(scores: pd.DataFrame, freq: str = "M") -> pd.DataFrame:
    """Per site and period: images by quality, share of usable images with hot pixels,
    median hot area on clear images. ``hot_share`` uses every image with a hot pixel
    (clear, partial or cloudy: heat seen through cloud is real) and clear images without one
    (a zero on a partly or fully cloudy image is not counted)."""
    s = scores[scores["quality"].isin(["clear", "partial", "cloudy"])].copy()
    s["period"] = pd.to_datetime(s["acquired_at"], utc=True).dt.tz_localize(None).dt.to_period(freq)
    s["usable"] = s["hot_seen"] | s["cold_seen"]
    g = s.groupby(["site_id", "period"])
    out = pd.DataFrame({
        "n_images": g.size(),
        "n_clear": g["quality"].apply(lambda q: (q == "clear").sum()),
        "n_usable": g["usable"].sum(),
        "n_hot": g["hot_seen"].sum(),
        "hot_area_clear_m2": g.apply(lambda d: d.loc[d["quality"] == "clear", "hot_area_m2"].median(),
                                     include_groups=False),
    })
    out["hot_share"] = out["n_hot"] / out["n_usable"].where(out["n_usable"] > 0)
    return out.reset_index()


QUALITY_RANK = {"clear": 0, "partial": 1, "cloudy": 2, "nodata": 3, "missing": 4}


def dedupe_scores(scores: pd.DataFrame, within_s: int = 180) -> pd.DataFrame:
    """One row per acquisition: overlapping Landsat WRS-2 rows and Sentinel-2 tiles give the
    same overpass twice, seconds apart. Keep the best-quality copy."""
    s = scores.copy()
    s["_t"] = pd.to_datetime(s["acquired_at"], utc=True)
    s["_q"] = s["quality"].map(QUALITY_RANK).fillna(5)
    s = s.sort_values(["site_id", "sensor", "platform", "_t"])
    gap = s.groupby(["site_id", "sensor", "platform"])["_t"].diff().dt.total_seconds()
    s["_g"] = (gap.isna() | (gap > within_s)).cumsum()
    s = s.sort_values(["_g", "_q", "nodata_frac"]).drop_duplicates("_g")
    return s.drop(columns=["_t", "_q", "_g"]).sort_values(["site_id", "acquired_at"]).reset_index(drop=True)
