# OrbitCu

OrbitCu is an open, reproducible pipeline that uses **free satellite data** to
measure, every day, whether the world's largest copper smelters are running.
The activity signal is turned into capacity-weighted indices, used to nowcast
refined copper supply before official statistics are published, and tested
inside point-in-time backtests of copper futures strategies.

CSE 406 Software Engineering term project, team CoPPernicus.

> Status (November 2026): the **smelter registry** and the **satellite
> ingestion layer** are built and prototyped on three sites. Detection, fusion,
> indices, validation, market data, backtest and dashboard are still to do.
> See [`docs/status.md`](docs/status.md).

## Documentation

| Document | Content |
|---|---|
| [`docs/overview.md`](docs/overview.md) | Purpose, client needs, scope, requirements |
| [`docs/architecture.md`](docs/architecture.md) | The nine pipeline stages and how data flows between them |
| [`docs/data_sources.md`](docs/data_sources.md) | Satellite and market data sources, access, limits |
| [`docs/registry.md`](docs/registry.md) | How the 29 smelters were selected, sourced and geolocated |
| [`docs/setup.md`](docs/setup.md) | Installation, API keys, command line, tests |
| [`docs/modules.md`](docs/modules.md) | Reference of the Python package `orbitcu` |
| [`docs/ingestion.md`](docs/ingestion.md) | Ingestion method in detail (AOIs, point-in-time rules, blockers) |
| [`docs/prototype_results.md`](docs/prototype_results.md) | One-year prototype on Glogow, Hamburg and Kamoa-Kakula |
| [`docs/status.md`](docs/status.md) | What is done, what remains, roadmap |
| [`docs/status_map.html`](docs/status_map.html) | Interactive smelter status map (VIIRS, Jan 2025 - Oct 2026): download and open in a browser |

## Repository layout

```
data/registry/               smelter registry, exclusions, site coordinates
src/orbitcu/                 Python package (sites, ingestion, geolocation, status map)
  ingest/                    one module per data source + point-in-time rules
  templates/                 status map page and embedded base map
tests/                       unit tests (no network)
docs/                        project documentation
```

## Quick start

Linux / macOS (bash):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,era5,s5p]"
export FIRMS_MAP_KEY=...      # free key: https://firms.modaps.eosdis.nasa.gov/api/map_key/
export CDSAPI_KEY=...         # Copernicus CDS personal access token
pytest                        # 20 unit tests, no network
orbitcu optical --sensor sentinel2 --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
orbitcu firms   --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev,era5,s5p]"
$env:FIRMS_MAP_KEY = "..."
$env:CDSAPI_KEY = "..."
pytest
orbitcu optical --sensor sentinel2 --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
orbitcu firms   --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
```

Windows (cmd): activate with `.venv\Scripts\activate.bat` and set keys with
`set FIRMS_MAP_KEY=...` (no quotes, no spaces around `=`). With Anaconda, you
can skip the venv and run the same commands in an activated conda environment.

Variables set this way last only for the current terminal. To keep them,
use `setx FIRMS_MAP_KEY "..."` on Windows (then open a new terminal) or add the
`export` lines to `~/.bashrc`.

Outputs go to `output/<site_id>/` (git-ignored). Details in
[`docs/setup.md`](docs/setup.md).

## Team

| Member | Course role | Modules |
|---|---|---|
| Robin | User (analyst / trader) | Market data, nowcast, backtest, dashboard |
| Ethan | Customer (trading desk) | SO2 estimation, fusion, activity indices |
| Radu | Software developer | Satellite ingestion, thermal detection |
| Baptiste | Development manager | Kubernetes, CI/CD, smelter registry, data contracts |
