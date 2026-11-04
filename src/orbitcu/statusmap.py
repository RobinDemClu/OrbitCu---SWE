"""Interactive map of smelter locations and monthly activity status (FR-8 seed).

Builds one self-contained HTML page (no tile server, no network at view time):

* the 29 registry smelters at their furnace points, sized by capacity;
* for each month, the number of distinct days with a VIIRS S-NPP detection
  within ``radius_km`` of the furnace (``activity_check.monthly_detection_days``);
* the page turns those counts into a status for a chosen month and window
  (1, 3 or 12 months), relative to each site's own typical level.

Usage::

    python -m orbitcu.statusmap --firms-dir cache/firms_hist --start 2025-01 --end 2026-10 \
        --out output/status_map.html

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
            # typical level = median of monthly detection days over the period
            "baseline": float(np.median(days)) if days else 0.0,
            "has_data": path.exists(),
        })
    return {
        "months": months,
        "radius_km": radius_km,
        "sensor": "VIIRS S-NPP 375 m (NASA FIRMS; SP archive, NRT for the latest months)",
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="orbitcu-statusmap")
    p.add_argument("--firms-dir", type=Path, default=Path("cache/firms_hist"))
    p.add_argument("--start", default="2025-01")
    p.add_argument("--end", default="2026-10")
    p.add_argument("--radius-km", type=float, default=1.5)
    p.add_argument("--out", type=Path, default=Path("output/status_map.html"))
    p.add_argument("--json", type=Path, default=None, help="also write the data as JSON")
    a = p.parse_args(argv)
    data = build_data(a.firms_dir, a.start, a.end, a.radius_km)
    if a.json:
        a.json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(render(data, a.out))


if __name__ == "__main__":
    main()
