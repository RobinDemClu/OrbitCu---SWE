"""``orbitcu so2-copper``: SO2 emissions -> copper estimates -> consistency checks.

Reads the outputs of ``orbitcu s5p-batch`` (``<data>/s5p_batch/*.parquet``)
and of the ERA5 point series (``<data>/era5_ts/*.parquet``), then writes to
``--out``:

* ``neighbour_sources.csv``     - best-fitting SO2 source position per site
  (first 12 months); offsets > 7.5 km get a second, jointly fitted source;
* ``so2_emissions_monthly.csv``  - fitted emission per site and month;
* ``so2_emissions_annual.csv``   - same, fitted on whole calendar years
  (lower noise), with the detectability t-statistic;
* ``copper_estimates_quarterly.csv`` - copper estimate per site and quarter;
* ``backtest_reported.csv``      - point-in-time predictions vs reported output;
* ``event_checks.csv``           - emission during known shutdowns / start-ups
  versus the same site's other months.

Example::

    orbitcu so2-copper --data output --out output/so2_copper
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from . import copper
from . import emissions as em
from .ingest import s5p_batch
from .sites import REPO_ROOT, load_sites

REF_DIR = REPO_ROOT / "data" / "reference"


def event_checks(monthly: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for e in events.itertuples(index=False):
        m = monthly[(monthly["site_id"] == e.site_id) & monthly["E_kt_yr"].notna()].copy()
        if m.empty:
            continue
        m["month"] = pd.PeriodIndex(m["period"], freq="M")
        inside = (m["month"] >= pd.Period(e.start, "M")) & (m["month"] <= pd.Period(e.end, "M"))
        a, b = m[inside], m[~inside]
        if a.empty or b.empty:
            continue
        ea = np.average(a["E_kt_yr"], weights=a["n_pixels"])
        eb = np.average(b["E_kt_yr"], weights=b["n_pixels"])
        sa = np.sqrt(np.sum((a["n_pixels"] / a["n_pixels"].sum() * a["E_se_kt_yr"]) ** 2))
        sb = np.sqrt(np.sum((b["n_pixels"] / b["n_pixels"].sum() * b["E_se_kt_yr"]) ** 2))
        z = (ea - eb) / np.hypot(sa, sb)
        ok = (z < 0) if e.expected_effect == "down" else (z > 0)
        rows.append({"site_id": e.site_id, "event": e.event, "start": e.start, "end": e.end,
                     "expected": e.expected_effect, "E_event_kt_yr": ea, "E_other_kt_yr": eb,
                     "z": z, "direction_ok": bool(ok), "significant_2sigma": bool(ok and abs(z) > 2),
                     "months_event": len(a), "months_other": len(b)})
    return pd.DataFrame(rows)


def main(argv=None):
    p = argparse.ArgumentParser(prog="orbitcu so2-copper")
    p.add_argument("--data", type=Path, default=Path("output"), help="folder with s5p_batch/ and era5_ts/")
    p.add_argument("--out", type=Path, default=Path("output/so2_copper"))
    p.add_argument("--sites", nargs="*")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--tau", type=float, default=6.0, help="SO2 lifetime in hours")
    p.add_argument("--sigma", type=float, default=10.0, help="plume width in km")
    p.add_argument("--radius", type=float, default=40.0, help="fit radius in km")
    a = p.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    cfg = em.FitConfig(tau_h=a.tau, sigma_km=a.sigma, radius_km=a.radius)
    sites = load_sites(ids=a.sites or None)
    px = s5p_batch.load(a.data, a.start, a.end)
    print(f"{len(px):,} pixels, {px['site_id'].nunique()} sites, "
          f"{px['pixel_time_utc'].min():%Y-%m} .. {px['pixel_time_utc'].max():%Y-%m}")

    nb = em.neighbour_sources(px, sites, a.data / "era5_ts", cfg)
    nb.to_csv(a.out / "neighbour_sources.csv", index=False)
    monthly = em.monthly_emissions(px, sites, a.data / "era5_ts", cfg, freq="M", neighbours=nb)
    monthly.to_csv(a.out / "so2_emissions_monthly.csv", index=False)
    annual = em.monthly_emissions(px, sites, a.data / "era5_ts", cfg, freq="Y", neighbours=nb)
    annual.to_csv(a.out / "so2_emissions_annual.csv", index=False)

    rep = copper.load_reported(REF_DIR / "reported_output.csv")
    est = copper.site_estimates(monthly, sites, rep)
    est.to_csv(a.out / "copper_estimates_quarterly.csv", index=False)
    pairs = copper.reported_vs_satellite(monthly, rep)
    pairs.to_csv(a.out / "reported_vs_satellite.csv", index=False)
    bt = copper.pit_backtest(pairs)
    bt.to_csv(a.out / "backtest_reported.csv", index=False)
    ev = event_checks(monthly, pd.read_csv(REF_DIR / "known_events.csv"))
    ev.to_csv(a.out / "event_checks.csv", index=False)

    pd.set_option("display.width", 200)
    print("\nAnnual SO2 emission (kt/yr, +/- 1 s.e.):")
    print(annual.pivot(index="site_id", columns="period", values="E_kt_yr").round(1))
    print("\nEvent checks:")
    print(ev.round(2).to_string(index=False) if not ev.empty else "  none")
    print("\nPoint-in-time backtest vs reported output:")
    print(bt.round(1).to_string(index=False) if not bt.empty else "  none")
    print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
