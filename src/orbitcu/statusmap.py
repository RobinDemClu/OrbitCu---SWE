"""Interactive map of smelter locations and monthly activity status (FR-8 seed).

Builds one self-contained HTML page (no tile server, no network at view time):

* the 29 registry smelters at their furnace points, sized by capacity;
* for each month, the number of distinct days with a VIIRS S-NPP detection
  within ``radius_km`` of the furnace (``activity_check.monthly_detection_days``);
* the page turns those counts into a status for a chosen month and window
  (1, 3 or 12 months), relative to each site's own typical level.

Usage (downloads the VIIRS history it needs, then writes the page)::

    orbitcu statusmap                       # last 22 complete months -> output/status_map.html
    orbitcu statusmap --start 2025-01 --end 2026-10 --open

VIIRS detections are cached per site in ``cache/firms_hist/<site_id>.parquet``
so a second run is instant; ``--refresh`` downloads them again.

The base map is Natural Earth 1:50m (public domain) via the ``world-atlas``
TopoJSON package, embedded in the page.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
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
        path = firms_dir / f"{r.id}.parquet"
        df = pd.read_parquet(path) if path.exists() else pd.DataFrame()
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
            "has_data": path.exists(),
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


def default_months(n: int = 22) -> tuple[str, str]:
    """The last ``n`` complete calendar months, as ('YYYY-MM', 'YYYY-MM')."""
    last = pd.Period(datetime.now(timezone.utc).strftime("%Y-%m"), freq="M") - 1
    return str(last - (n - 1)), str(last)


def fetch_history(firms_dir: Path, start: str, end: str, ids: list[str] | None = None,
                  refresh: bool = False, sensors=("VIIRS_SNPP",)) -> None:
    """Download VIIRS detections in each site's thermal box for [start, end]
    into ``firms_dir/<site_id>.parquet``; sites already cached are skipped."""
    from .ingest import firms
    from .sites import load_sites

    d0 = pd.Period(start, freq="M").start_time.date()
    d1 = pd.Period(end, freq="M").end_time.date()
    firms_dir.mkdir(parents=True, exist_ok=True)
    sites = load_sites(ids=ids)
    for i, s in enumerate(sites, 1):
        path = firms_dir / f"{s.id}.parquet"
        if path.exists() and not refresh:
            print(f"[{i}/{len(sites)}] {s.id}: cached")
            continue
        df = firms.fetch_area(s.thermal_bbox(), d0, d1, sensors=sensors)
        df.to_parquet(path)
        print(f"[{i}/{len(sites)}] {s.id}: {len(df)} detections")


def main(argv=None):
    p = argparse.ArgumentParser(prog="orbitcu statusmap",
                                description="Build the interactive smelter status map (HTML).")
    p.add_argument("--start", default=None, help="first month YYYY-MM (default: 22 months back)")
    p.add_argument("--end", default=None, help="last month YYYY-MM (default: last complete month)")
    p.add_argument("--firms-dir", type=Path, default=Path("cache/firms_hist"))
    p.add_argument("--radius-km", type=float, default=1.5)
    p.add_argument("--out", type=Path, default=Path("output/status_map.html"))
    p.add_argument("--json", type=Path, default=None, help="also write the data as JSON")
    p.add_argument("--refresh", action="store_true", help="download VIIRS history again")
    p.add_argument("--no-download", action="store_true", help="use the cache only")
    p.add_argument("--open", action="store_true", help="open the page in the web browser")
    a = p.parse_args(argv)
    start, end = default_months()
    start, end = a.start or start, a.end or end
    if not a.no_download:
        fetch_history(a.firms_dir, start, end, refresh=a.refresh)
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
