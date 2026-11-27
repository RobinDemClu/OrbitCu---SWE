"""Satellite thumbnails of each smelter for the results map.

For each registry site, take the most recent clear Sentinel-2 image (preferring one
with a confirmed hot spot in the last 12 months) from the image-score table, and
save two 4.4 x 4.4 km crops through the Planetary Computer data API:
true colour (visual asset) and short-wave infrared (B12, B11, B8A), where hot
furnaces show as bright spots.

    python scripts/build_site_thumbnails.py --scores <image_scores_all.parquet>

Writes data/registry/thumbnails/<site>_rgb.jpg, <site>_swir.jpg and thumbnails.csv.
"""
from __future__ import annotations

import argparse
import io
import math
from pathlib import Path

import pandas as pd
import requests
from PIL import Image, ImageOps

API = "https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/{bbox}/{px}x{px}.png"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "registry" / "thumbnails"


def pick_items(scores: pd.DataFrame) -> pd.DataFrame:
    s = scores[(scores.sensor == "sentinel2") & (scores.quality == "clear")].copy()
    s["acquired_at"] = pd.to_datetime(s["acquired_at"], utc=True, format="mixed")
    last = s["acquired_at"].max()
    rows = []
    for sid, g in s.groupby("site_id"):
        g = g.sort_values("acquired_at")
        hot = g[(g.hot_seen == True) & (g.acquired_at > last - pd.Timedelta(days=365))]  # noqa: E712
        rows.append((hot if len(hot) else g).iloc[-1])
    return pd.DataFrame(rows)


def fetch(item: str, lat: float, lon: float, params: list[tuple[str, str]], half_km: float = 2.2, px: int = 320) -> Image.Image:
    dlat = half_km / 111.32
    dlon = dlat / math.cos(math.radians(lat))
    bbox = f"{lon - dlon:.5f},{lat - dlat:.5f},{lon + dlon:.5f},{lat + dlat:.5f}"
    q = [("collection", "sentinel-2-l2a"), ("item", item)] + params
    r = requests.get(API.format(bbox=bbox, px=px), params=q, timeout=120)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--scores", type=Path, required=True)
    p.add_argument("--sites", type=Path, default=ROOT / "data" / "registry" / "smelter_sites.csv")
    a = p.parse_args(argv)
    sites = pd.read_csv(a.sites).set_index("id")
    items = pick_items(pd.read_parquet(a.scores))
    OUT.mkdir(parents=True, exist_ok=True)
    meta = []
    for r in items.itertuples(index=False):
        if r.site_id not in sites.index:
            continue
        lat, lon = float(sites.at[r.site_id, "lat"]), float(sites.at[r.site_id, "lon"])
        rgb = fetch(r.item_id, lat, lon, [("assets", "visual")])
        rgb = ImageOps.autocontrast(rgb, cutoff=1)
        rgb.save(OUT / f"{r.site_id}_rgb.jpg", quality=78)
        swir = fetch(r.item_id, lat, lon, [("assets", "B12"), ("assets", "B11"), ("assets", "B8A"), ("rescale", "1000,6000")])
        swir.save(OUT / f"{r.site_id}_swir.jpg", quality=78)
        meta.append({"site_id": r.site_id, "item_id": r.item_id, "acquired": str(r.acquired_at)[:10],
                     "hot_seen": bool(r.hot_seen), "half_km": 2.2})
        print(r.site_id, r.item_id)
    pd.DataFrame(meta).sort_values("site_id").to_csv(OUT / "thumbnails.csv", index=False)


if __name__ == "__main__":
    main()
