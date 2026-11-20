"""Interactive map of smelter locations and monthly activity status (FR-8 seed).

Builds one self-contained HTML page (no tile server, no network at view time):

* the 29 registry smelters at their furnace points, sized by capacity;
* for each month, the number of distinct days with a VIIRS S-NPP detection
  within ``radius_km`` of the furnace (``activity_check.monthly_detection_days``);
* the page turns those counts into a status for a chosen month and window
  (1, 3 or 12 months), relative to each site's own typical level.

Usage (downloads the VIIRS history it needs, then writes the page)::

    orbitcu statusmap                       # last 12 complete months -> output/status_map.html
    orbitcu statusmap --start 2025-01 --end 2026-10 --open

VIIRS detections are cached per site and month in
``cache/firms_hist/<site_id>/<YYYY-MM>.parquet``, so a second run only downloads
the new month; ``--refresh`` downloads them again.

The base map is Natural Earth 1:50m (public domain) via the ``world-atlas``
TopoJSON package, embedded in the page.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .activity_check import monthly_detection_days
from .sites import REGISTRY_DIR, SITES_CSV

TEMPLATES = Path(__file__).resolve().parent / "templates"


def typical_level(days: list[int], min_months: int = 3) -> float:
    """Typical monthly detection days of a site: the median over the months with
    at least one detection. Using only months with heat keeps a long outage from
    dragging the reference down (an outage must read as an outage). Fewer than
    ``min_months`` such months means VIIRS sees the plant too rarely: 0."""
    hot = [d for d in days if d > 0]
    return float(np.median(hot)) if len(hot) >= min_months else 0.0


def build_data(firms_dir: Path, start: str, end: str, radius_km: float = 1.5,
               sites_csv: Path = SITES_CSV) -> dict:
    sites = pd.read_csv(sites_csv)
    reg = pd.read_csv(REGISTRY_DIR / "smelter_registry.csv")[["id", "operator_activity_evidence"]]
    sites = sites.merge(reg, on="id", how="left")
    months = [str(p) for p in pd.period_range(start, end, freq="M")]
    out = []
    for r in sites.itertuples(index=False):
        df = load_cached(firms_dir, r.id)
        m = monthly_detection_days(df, r.lat, r.lon, radius_km, start=start, end=end)
        days = [int(v) for v in m.values]
        out.append({
            "id": r.id,
            "name": r.smelter,
            "country": r.country,
            "operator": r.operator,
            "capacity_kt": None if pd.isna(r.capacity_kt_cu) else float(r.capacity_kt_cu),
            "grade": r.operator_check,
            "confidence": r.geoloc_confidence,
            "lat": float(r.lat),
            "lon": float(r.lon),
            "note": r.geoloc_note,
            "days": days,
            "baseline": typical_level(days),
            "has_data": not df.empty,
        })
    return {
        "months": months,
        "radius_km": radius_km,
        "sensor": "VIIRS S-NPP 375 m (NASA FIRMS ; archive SP, puis NRT pour les derniers mois)",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "sites": out,
    }


def render(data: dict, out: Path, template: Path = TEMPLATES / "status_map.html") -> Path:
    html = template.read_text(encoding="utf-8")
    world = (TEMPLATES / "countries-50m.json").read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("/*__WORLD__*/null", world)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def default_months(n: int = 12) -> tuple[str, str]:
    """The last ``n`` complete calendar months, as ('YYYY-MM', 'YYYY-MM')."""
    last = pd.Period(datetime.now(timezone.utc).strftime("%Y-%m"), freq="M") - 1
    return str(last - (n - 1)), str(last)


def _site_cache(firms_dir: Path, site_id: str, month: str) -> Path:
    return firms_dir / site_id / f"{month}.parquet"


def load_cached(firms_dir: Path, site_id: str) -> pd.DataFrame:
    """All cached VIIRS detections of one site (one Parquet file per month)."""
    files = sorted((firms_dir / site_id).glob("*.parquet"))
    legacy = firms_dir / f"{site_id}.parquet"  # older one-file-per-site layout
    if legacy.exists():
        files.append(legacy)
    parts = [pd.read_parquet(f) for f in files]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_history(firms_dir: Path, start: str, end: str, ids: list[str] | None = None,
                  refresh: bool = False, sensor: str = "VIIRS_SNPP", workers: int = 12) -> None:
    """Download VIIRS detections in each site's thermal box, month by month,
    into ``firms_dir/<site_id>/<YYYY-MM>.parquet``.

    Months already on disk are skipped (except the current month, which is
    still filling up), so an interrupted run resumes where it stopped and a
    later run only downloads the new months. All requests of all sites go
    through one thread pool: FIRMS answers slowly but in parallel."""
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .ingest import firms
    from .sites import load_sites

    months = [str(p) for p in pd.period_range(start, end, freq="M")]
    now = datetime.now(timezone.utc).strftime("%Y-%m")
    todo = []  # (site, month, path)
    for s in load_sites(ids=ids):
        for m in months:
            path = _site_cache(firms_dir, s.id, m)
            if refresh or m >= now or not path.exists():
                todo.append((s, m, path))
    if not todo:
        print("VIIRS history: everything already cached")
        return

    # One FIRMS request covers at most 5 days: split each (site, month).
    plan = firms.plan_requests(sensor, pd.Period(start, freq="M").start_time.date(),
                               pd.Period(end, freq="M").end_time.date())
    jobs = []  # (task index, source, day, n_days)
    for k, (s, m, _) in enumerate(todo):
        p = pd.Period(m, freq="M")
        for src, d, n in plan:
            # clip each planned chunk to this month
            d0, d1 = max(d, p.start_time.date()), min(d + timedelta(days=n - 1), p.end_time.date())
            if d0 <= d1:
                jobs.append((k, src, d0, (d1 - d0).days + 1))
    print(f"VIIRS history: {len(todo)} site-months to download ({len(jobs)} requests, "
          f"{workers} in parallel). Already downloaded months are kept.", flush=True)

    parts: dict[int, list] = {k: [] for k in range(len(todo))}
    left = {k: 0 for k in range(len(todo))}
    for k, *_ in jobs:
        left[k] += 1
    t0, done = time.time(), 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(firms._fetch_one, src, todo[k][0].thermal_bbox(), d, n): k for k, src, d, n in jobs}
        for f in as_completed(futs):
            k = futs[f]
            parts[k].append(f.result())
            left[k] -= 1
            done += 1
            if left[k] == 0:  # month complete: write it so progress survives an interruption
                s, m, path = todo[k]
                got = [p for p in parts.pop(k) if not p.empty]
                df = firms.normalise(pd.concat(got, ignore_index=True)) if got else pd.DataFrame()
                path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(path)
            el = time.time() - t0
            eta = el / done * (len(jobs) - done)
            sys.stdout.write(f"\r  {done}/{len(jobs)} requests, about {eta / 60:.0f} min left   ")
            sys.stdout.flush()
    print()


def main(argv=None):
    p = argparse.ArgumentParser(prog="orbitcu statusmap",
                                description="Build the interactive smelter status map (HTML).")
    p.add_argument("--months", type=int, default=12, help="number of months shown (default 12)")
    p.add_argument("--start", default=None, help="first month YYYY-MM (overrides --months)")
    p.add_argument("--end", default=None, help="last month YYYY-MM (default: last complete month)")
    p.add_argument("--firms-dir", type=Path, default=Path("cache/firms_hist"))
    p.add_argument("--radius-km", type=float, default=1.5)
    p.add_argument("--out", type=Path, default=Path("output/status_map.html"))
    p.add_argument("--json", type=Path, default=None, help="also write the data as JSON")
    p.add_argument("--refresh", action="store_true", help="download VIIRS history again")
    p.add_argument("--no-download", action="store_true", help="use the cache only")
    p.add_argument("--workers", type=int, default=12, help="parallel FIRMS requests (default 12)")
    p.add_argument("--open", action="store_true", help="open the page in the web browser")
    a = p.parse_args(argv)
    start, end = default_months(a.months)
    start, end = a.start or start, a.end or end
    if not a.no_download:
        fetch_history(a.firms_dir, start, end, refresh=a.refresh, workers=a.workers)
    data = build_data(a.firms_dir, start, end, a.radius_km)
    if a.json:
        a.json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    out = render(data, a.out).resolve()
    print(f"Status map ({start} to {end}) -> {out}")
    if a.open:
        import webbrowser
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
