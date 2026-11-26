"""Backfill of per-image heat scores (``orbitcu image-scores``).

For each site, sensor and year: list the Sentinel-2 / Landsat scenes over the site's thermal
box, fetch each clip through the Planetary Computer data API, score it (``hotspot``) and keep
only the score row (clips are not stored: they can be fetched again). One parquet file per
site, sensor and year, so an interrupted run resumes where it stopped.
"""
from __future__ import annotations

import argparse
import io
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from . import hotspot
from .ingest import optical
from .sites import load_sites


def score_scene(site, sensor, row) -> dict:
    import rasterio

    rec = {"site_id": site.id, "sensor": sensor.key, "item_id": row.item_id, "platform": row.platform,
           "acquired_at": row.acquired_at, "available_at": row.available_at,
           "scene_cloud_pct": row.scene_cloud_pct, "processing_baseline": row.processing_baseline}
    try:
        data = optical.fetch_clip(site, sensor, row.item_id, row.proj_code)
        with rasterio.open(io.BytesIO(data)) as src:
            rec.update(hotspot.score_image(sensor.key, src.read(), row.processing_baseline, abs(src.transform.a)))
    except Exception as e:  # noqa: BLE001 - one bad scene must not stop the run
        rec.update({"quality": "missing", "error": f"{type(e).__name__}: {e}"[:200]})
    return rec


def run(sites, sensors, years, out: Path, workers: int = 16, max_cloud: float | None = 95) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cat = optical.client()
    for s in sites:
        for sen in sensors:
            for y in years:
                f = out / f"{s.id}_{sen.key}_{y}.parquet"
                if f.exists():
                    continue
                t0 = time.time()
                try:
                    sc = optical.search_scenes(s, sen, f"{y}-01-01", f"{y}-12-31T23:59:59Z", max_cloud=max_cloud, cat=cat)
                except Exception as e:  # noqa: BLE001
                    print(f"SKIPPED search {s.id} {sen.key} {y}: {type(e).__name__}: {e}", flush=True)
                    continue
                if sc.empty:
                    pd.DataFrame(columns=["site_id"]).to_parquet(f)
                    print(f"{s.id} {sen.key} {y}: 0 scenes", flush=True)
                    continue
                with ThreadPoolExecutor(workers) as ex:
                    rows = list(ex.map(lambda r: score_scene(s, sen, r), sc.itertuples(index=False)))
                d = pd.DataFrame(rows)
                n_miss = int((d["quality"] == "missing").sum())
                if n_miss > 0.2 * len(d):   # network trouble: do not freeze a bad year, retry later
                    print(f"RETRY LATER {s.id} {sen.key} {y}: {n_miss}/{len(d)} failed", flush=True)
                    continue
                d.to_parquet(f)
                print(f"{s.id} {sen.key} {y}: {len(d)} images, {int(d['hot_seen'].fillna(False).sum())} hot, "
                      f"{(d['quality'] == 'clear').sum()} clear, {n_miss} failed, {time.time() - t0:.0f} s", flush=True)


def load(out: Path) -> pd.DataFrame:
    fs = [pd.read_parquet(f) for f in sorted(Path(out).glob("*.parquet"))]
    fs = [f for f in fs if len(f)]
    return hotspot.dedupe_scores(pd.concat(fs, ignore_index=True)) if fs else pd.DataFrame()


def main(argv=None):
    p = argparse.ArgumentParser(prog="orbitcu image-scores")
    p.add_argument("--sites", nargs="*", help="site ids (default: all)")
    p.add_argument("--sensors", nargs="*", default=["sentinel2", "landsat"], choices=["sentinel2", "landsat"])
    p.add_argument("--start-year", type=int, default=2018)
    p.add_argument("--end-year", type=int, default=2026)
    p.add_argument("--out", type=Path, default=Path("output/image_scores"))
    p.add_argument("--workers", type=int, default=16)
    a = p.parse_args(argv)
    sites = load_sites(ids=a.sites or None)
    run(sites, [optical.SENSORS[k] for k in a.sensors], range(a.start_year, a.end_year + 1), a.out, a.workers)
    d = load(a.out)
    if len(d):
        d.to_csv(a.out / "image_scores_all.csv", index=False)
        hotspot.site_image_summary(d).to_csv(a.out / "image_scores_monthly.csv", index=False)
        print(f"total {len(d)} images -> {a.out / 'image_scores_all.csv'}", flush=True)
