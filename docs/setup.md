# Setup and usage

## Requirements

* Python 3.10 or later
* Network access to `planetarycomputer.microsoft.com` and
  `firms.modaps.eosdis.nasa.gov`; for SO2 and winds also
  `sentinel5euwest.blob.core.windows.net`, `cds.climate.copernicus.eu` and
  `object-store.os-api.cci2.ecmwf.int`

## Installation

```bash
git clone https://github.com/RobinDemClu/OrbitCu---SWE.git
cd OrbitCu---SWE
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\Activate.ps1    # Windows PowerShell
# .venv\Scripts\activate.bat    # Windows cmd
pip install -e ".[dev,era5,s5p]"
```

Extras: `era5` (cdsapi, xarray, netCDF4), `s5p` (xarray, h5netcdf, fsspec),
`dev` (pytest).

## API keys

| Variable | Where to get it |
|---|---|
| `FIRMS_MAP_KEY` | free, https://firms.modaps.eosdis.nasa.gov/api/map_key/ |
| `CDSAPI_KEY` | Copernicus CDS account, personal access token; accept the ERA5 licence once |

Set them in the terminal before running the commands:

| Shell | Command |
|---|---|
| bash / zsh (Linux, macOS) | `export FIRMS_MAP_KEY=...` |
| PowerShell (Windows) | `$env:FIRMS_MAP_KEY = "..."` |
| cmd (Windows) | `set FIRMS_MAP_KEY=...` |
| permanent on Windows | `setx FIRMS_MAP_KEY "..."`, then open a new terminal |

The Planetary Computer needs no key. Keys can be put in a `.env` file, which
is git-ignored. Never commit them.

## Command line

```bash
orbitcu optical --sensor sentinel2 --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
orbitcu optical --sensor landsat   --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
orbitcu firms   --sites PL-GLOGOW DE-HAMBURG --start 2025-11-01 --end 2026-10-31
orbitcu s5p     --sites DE-HAMBURG --start 2026-06-01 --end 2026-06-30
orbitcu era5    --sites DE-HAMBURG --start 2025-11-01 --end 2026-10-31
```

`--sites` takes registry ids (see `data/registry/smelter_sites.csv`); without
it, all sites are processed. `--out` changes the output folder (default
`output/`).

Outputs, per site: one Parquet manifest per source (with `acquired_at`,
`available_at`, `pit_basis` and quality fractions) and GeoTIFF clips of the
thermal area for the optical sensors.

## Status map

```bash
orbitcu statusmap --open
```

What it does:

1. downloads one VIIRS S-NPP detection file per smelter from NASA FIRMS
   (3 x 3 km box around the furnace, last 22 complete months) into
   `cache/firms_hist/<site_id>.parquet`; sites already cached are skipped;
2. counts, per month, the days with at least one detection within 1.5 km of
   the furnace and compares them with the site's usual level;
3. writes one self-contained page, `output/status_map.html`, and opens it in
   the browser (`--open`). The page needs internet only for its fonts and the
   d3 drawing library.

Options: `--start 2025-01 --end 2026-10` (months), `--refresh` (download
again), `--no-download` (cache only), `--out <file>`. Needs `FIRMS_MAP_KEY`.
The first run takes about 15-30 minutes because of the FIRMS quota.
`output/` and `cache/` are git-ignored: the map is always generated, never
committed.

## Tests

```bash
pytest
```

20 unit tests, no network access needed.

## Conventions

* Work happens on the `developpement` branch; changes reach `main` through
  pull requests.
* Generated data (`output/`, `cache/`) is never committed.
