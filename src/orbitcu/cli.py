"""Command-line entry point: ``orbitcu <command> ...``.

Examples::

    orbitcu firms   --sites DE-HAMBURG PL-GLOGOW --start 2025-11-01 --end 2026-10-31
    orbitcu optical --sensor sentinel2 --sites DE-HAMBURG --start 2025-11-01 --end 2026-10-31
    orbitcu s5p     --sites DE-HAMBURG --start 2026-06-01 --end 2026-06-30
    orbitcu s5p-batch --start 2025-01 --end 2026-10      # all sites, one read per orbit
    orbitcu era5    --sites DE-HAMBURG --start 2025-11-01 --end 2026-10-31
    orbitcu statusmap --open

Outputs go to ``--out`` (default ``output/``): one Parquet manifest per
(site, source) plus GeoTIFF clips for the optical sensors.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from .sites import load_sites


def _dates(a):
    return date.fromisoformat(a.start), date.fromisoformat(a.end)


def cmd_firms(a):
    from .ingest import firms

    start, end = _dates(a)
    for s in load_sites(ids=a.sites or None):
        df = firms.fetch_area(s.thermal_bbox() if not a.wide else s.so2_bbox(), start, end)
        out = a.out / s.id / "firms.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not df.empty:
            df.insert(0, "site_id", s.id)
        df.to_parquet(out)
        print(f"{s.id}: {len(df)} detections -> {out}")


def cmd_optical(a):
    from .ingest import optical

    sensor = optical.SENSORS[a.sensor]
    cat = optical.client()
    for s in load_sites(ids=a.sites or None):
        df = optical.ingest_site(s, sensor, a.start, a.end, a.out, max_cloud=a.max_cloud, cat=cat)
        out = a.out / s.id / f"{sensor.key}_manifest.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out)
        print(f"{s.id}: {len(df)} {sensor.key} scenes -> {out}")


def cmd_s5p(a):
    from .ingest import s5p

    for s in load_sites(ids=a.sites or None):
        orbits = s5p.search_orbits(s, a.start, a.end, modes=tuple(a.modes))
        parts = []
        for o in orbits.itertuples(index=False):
            px = s5p.extract_orbit(s, o.href)
            if not px.empty:
                px["item_id"], px["mode"] = o.item_id, o.mode
                px["acquired_at"], px["available_at"] = o.acquired_at, o.available_at
                parts.append(px)
        out = a.out / s.id / "s5p_so2.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        (pd.concat(parts) if parts else pd.DataFrame()).to_parquet(out)
        orbits.drop(columns="href").to_parquet(a.out / s.id / "s5p_orbits.parquet")
        print(f"{s.id}: {len(orbits)} orbits, {sum(len(p) for p in parts)} pixels -> {out}")


def cmd_era5(a):
    from .ingest import era5

    start, end = _dates(a)
    months = pd.period_range(start, end, freq="M")
    for s in load_sites(ids=a.sites or None):
        tables = [era5.to_table(era5.retrieve(s, p.year, p.month, a.out), s.id) for p in months]
        out = a.out / s.id / "era5.parquet"
        pd.concat(tables).to_parquet(out)
        print(f"{s.id}: {sum(len(t) for t in tables)} rows -> {out}")


def cmd_s5p_batch(a):
    import logging

    from .ingest import s5p_batch

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    sites = load_sites(ids=a.sites or None)
    months = pd.period_range(a.start, a.end, freq="M")
    if a.reverse:
        months = months[::-1]
    for m in months:
        path = s5p_batch.ingest_month(sites, m, a.out, workers=a.workers, modes=tuple(a.modes))
        print(f"{m}: {len(pd.read_parquet(path))} pixels -> {path}", flush=True)


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["so2-copper"]:
        from . import so2_copper
        return so2_copper.main(argv[1:])
    if argv[:1] == ["statusmap"]:
        from . import statusmap
        return statusmap.main(argv[1:])
    p = argparse.ArgumentParser(prog="orbitcu", epilog="also: orbitcu statusmap [--open] (see orbitcu statusmap -h)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in [("firms", cmd_firms), ("optical", cmd_optical), ("s5p", cmd_s5p), ("era5", cmd_era5),
                     ("s5p-batch", cmd_s5p_batch)]:
        sp = sub.add_parser(name)
        sp.add_argument("--sites", nargs="*", help="site ids (default: all)")
        sp.add_argument("--start", required=True)
        sp.add_argument("--end", required=True)
        sp.add_argument("--out", type=Path, default=Path("output"))
        sp.set_defaults(fn=fn)
        if name == "optical":
            sp.add_argument("--sensor", choices=["sentinel2", "landsat"], required=True)
            sp.add_argument("--max-cloud", type=float, default=None)
        if name == "firms":
            sp.add_argument("--wide", action="store_true", help="use the 50 km SO2 box instead of the thermal box")
        if name == "s5p-batch":
            sp.add_argument("--workers", type=int, default=8)
            sp.add_argument("--reverse", action="store_true", help="newest month first")
        if name in ("s5p", "s5p-batch"):
            sp.add_argument("--modes", nargs="*", default=["OFFL"], choices=["OFFL", "NRTI", "RPRO"])
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
