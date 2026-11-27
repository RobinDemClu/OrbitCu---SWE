# OrbitCu

OrbitCu is an open, reproducible pipeline that uses **free satellite data** to
measure, every day, whether the world's largest copper smelters are running.
The activity signal is turned into capacity-weighted indices, used to nowcast
refined copper supply before official statistics are published, and tested
inside point-in-time backtests of copper futures strategies.

CSE 406 Software Engineering term project, team CoPPernicus.

## Documentation

**All the documentation is in one file:
[`docs/latex/orbitcu_report.pdf`](docs/latex/orbitcu_report.pdf)**
(LaTeX source `docs/latex/orbitcu_report.tex`). It covers the project goal and
requirements, architecture, data sources, smelter registry, what has been built,
how to use the code, difficulties and next steps.

> Status (20 November 2026): smelter registry done (29 sites), all 5 satellite
> data sources working, status map available, SO2 emission estimates in
> progress. Fusion, indices, market data, backtest and dashboard are still to do.

## Repository layout

```
data/registry/      smelter registry, exclusions, site coordinates
data/reference/     known events and published output (validation)
src/orbitcu/        Python package (sites, ingestion, geolocation, status map, SO2)
tests/              unit tests (no network)
docs/latex/         project documentation (LaTeX + PDF)
```

## Quick start

```bash
pip install -e ".[dev,era5,s5p,emissions]"
export FIRMS_MAP_KEY=...      # Windows PowerShell: $env:FIRMS_MAP_KEY = "..."
export CDSAPI_KEY=...         # Windows PowerShell: $env:CDSAPI_KEY = "..."
pytest                        # 24 unit tests, no network
orbitcu statusmap --open      # interactive smelter status map
orbitcu sitemap --data <data folder>   # results map (status, events, output, alerts, images, SO2)
```

All commands and options are described in the report, section "Using the code".

## Team

Baptiste, Radu, Ethan, Robin.
