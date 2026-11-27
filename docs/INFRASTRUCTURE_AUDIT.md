# OrbitCu — Infrastructure Audit

| | |
|---|---|
| Audit date | 27 November 2026 |
| Branch audited | `feature/kubernetes-infrastructure`, fast-forwarded to `origin/developpement` at commit `a7b3916` |
| Scope | Everything an infrastructure layer (Docker, Kubernetes, CI/CD, persistent storage, daily automation) has to build on |
| Method | Read all source files under `src/orbitcu/`, `scripts/`, `tests/`, `pyproject.toml`, `.gitignore`, `README.md` and the LaTeX report; installed the package and ran the test suite |

This document describes the repository **as it is**. Nothing here is a plan.
Plans are in [ARCHITECTURE.md](ARCHITECTURE.md) and the status of each item is in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

---

## 1. Repository and branches

| Branch | Head (27 Nov 2026) | Role |
|---|---|---|
| `main` | `0f90bc4` (20 Nov 2026) | Stable branch. Behind `developpement`. |
| `developpement` | `a7b3916` | Integration branch: the team's daily work happens here (stated in the report, section "Team and tools"). |
| `feature/kubernetes-infrastructure` | `a7b3916` + infrastructure docs | Branch for the infrastructure work. No application code changes. |

Top-level layout:

```
data/registry/      smelter registry, exclusions, site coordinates (CSV, ~44 kB)
data/reference/     known events, published output, official statistics (CSV, ~112 kB)
data/trading/       market price series and trading-test results (CSV/JSON, ~192 kB)
src/orbitcu/        Python package (CLI, ingestion, measures, models)
scripts/            8 stand-alone trading analysis scripts (trading_*.py)
tests/              unit tests (pytest, no network)
docs/latex/         project report (LaTeX source + compiled PDF + figures)
```

**Infrastructure files that exist:** none. There is no `Dockerfile`, no
`.dockerignore`, no `.github/` directory (so no GitHub Actions workflow), no
Kubernetes or Argo manifests, no lock file, and no `.env.example`.

## 2. Packaging and dependencies

Source: [`pyproject.toml`](../pyproject.toml).

- Build backend: setuptools ≥ 68, `src/` layout, package data `orbitcu/templates/*`.
- Python: `requires-python = ">=3.10"`.
- Entry point: `orbitcu = "orbitcu.cli:main"`.
- Core dependencies, all **unpinned**: `numpy`, `pandas`, `pyarrow`, `requests`,
  `pystac-client`, `planetary-computer`, `rasterio`, `shapely`, `pyproj`.
- Optional extras:

| Extra | Packages | Needed by |
|---|---|---|
| `era5` | `cdsapi>=0.7`, `xarray`, `netcdf4` | `orbitcu era5`, ERA5 helpers |
| `s5p` | `xarray`, `h5netcdf`, `h5py`, `fsspec`, `aiohttp` | `orbitcu s5p`, `orbitcu s5p-batch` |
| `emissions` | `scipy`, `xarray`, `netcdf4` | `orbitcu so2-copper` |
| `dev` | `pytest` | tests |

Documented install command (README and report): `pip install -e ".[dev,era5,s5p,emissions]"`.

Native libraries: `rasterio`, `pyproj`, `shapely`, `h5py` and `netcdf4` ship
manylinux wheels with GDAL/PROJ/GEOS/HDF5 bundled. No system package is
required on a standard x86-64 Linux image.

> **Finding A-1: no reproducible dependency set.** Every dependency is
> unpinned and there is no lock file, so two builds a week apart can install
> different versions. A container image built by CI must use a lock or
> constraints file to be reproducible.

> **Finding A-2: the code finds its data through the source tree.**
> `src/orbitcu/sites.py` sets `REPO_ROOT = Path(__file__).resolve().parents[2]`
> and reads `data/registry/*.csv` and `data/reference/*.csv` from there.
> It works with an editable install (`pip install -e .`) or when the code is
> run from the repository. With a regular `pip install .`, `__file__` points
> into `site-packages` and the registry is not found. **A container must keep
> the repository layout, for example with an editable install in `/app`.**

## 3. Command-line interface

Source: [`src/orbitcu/cli.py`](../src/orbitcu/cli.py). The `main()` function
sends three commands to their own modules before argparse runs. That is why
`orbitcu --help` lists only five subcommands.

| Command | Module | Network / credentials | Default output | Arguments (verified) |
|---|---|---|---|---|
| `orbitcu firms` | `ingest.firms` | NASA FIRMS, `FIRMS_MAP_KEY` | `output/<site>/firms.parquet` | `--sites`, `--start`, `--end` (YYYY-MM-DD), `--out`, `--wide` |
| `orbitcu optical` | `ingest.optical` | Planetary Computer, anonymous | `output/<site>/<sensor>_manifest.parquet` + `output/<site>/<sensor>/<item_id>.tif` | `--sensor {sentinel2,landsat}`, `--sites`, `--start`, `--end`, `--out`, `--max-cloud` |
| `orbitcu s5p` | `ingest.s5p` | Planetary Computer, anonymous | `output/<site>/s5p_so2.parquet`, `output/<site>/s5p_orbits.parquet` | `--sites`, `--start`, `--end`, `--out`, `--modes {OFFL,NRTI,RPRO}` |
| `orbitcu era5` | `ingest.era5` | Copernicus CDS, `CDSAPI_KEY` (+ `CDSAPI_URL` optional) | `output/<site>/era5/<YYYY-MM>.nc`, `output/<site>/era5.parquet` | `--sites`, `--start`, `--end`, `--out` |
| `orbitcu s5p-batch` | `ingest.s5p_batch` | Planetary Computer, anonymous | `output/s5p_batch/<YYYY-MM>.parquet` | `--sites`, `--start`, `--end` (YYYY-MM), `--out`, `--workers` (8), `--reverse`, `--modes` |
| `orbitcu statusmap` | `statusmap` | NASA FIRMS, `FIRMS_MAP_KEY` (unless `--no-download`) | `cache/firms_hist/<site>/<YYYY-MM>.parquet`, `output/status_map.html` | `--months` (12), `--start`, `--end` (YYYY-MM), `--firms-dir`, `--radius-km`, `--out`, `--json`, `--refresh`, `--no-download`, `--workers` (6), `--open` |
| `orbitcu image-scores` | `image_scores` | Planetary Computer, anonymous | `output/image_scores/<site>_<sensor>_<year>.parquet`, `image_scores_all.csv`, `image_scores_monthly.csv` | `--sites`, `--sensors`, `--start-year` (2018), `--end-year` (2026), `--out`, `--workers` (16) |
| `orbitcu so2-copper` | `so2_copper` | none (reads local files) | `output/so2_copper/*.csv` | `--data` (needs `s5p_batch/` and `era5_ts/`), `--out`, `--sites`, `--start`, `--end`, `--tau`, `--sigma`, `--radius` |

All default paths are **relative to the current working directory**.

Functionality that exists as library code but has **no CLI command**:

| Function | What it produces | Consequence for automation |
|---|---|---|
| `ingest.era5.retrieve_timeseries` | `era5_ts/<site>_<start>_<end>.parquet` (point wind series) | `so2-copper` needs these files, and no command produces or refreshes them. |
| `ingest.era5.retrieve_cloud_timeseries` | `era5_tcc/<name>_<start>_<end>.parquet` | Used by the production/activity models; no command. |
| `ingest.era5.retrieve_overpass` | `era5_overpass/<site>/<year>.nc` | No command. |
| `ingest.firms.fetch_regions`, `split_regions` | Regional VIIRS backfill | No command. |
| `activity_index.*`, `production.*`, `trading.*` | Weekly status, world index, output estimates, trading tests | No command. `scripts/trading_*.py` read `activity_index/weekly_index.csv` from `$ORBITCU_DATA` (default `/mnt/project-files/data/`), and that file is produced outside the CLI. |

> **Finding A-3:** only the download commands, `statusmap`, `image-scores` and
> `so2-copper` can be scheduled with the CLI as it is. The weekly activity
> index, the production estimates and the trading tests **cannot** be
> automated yet without new entry points. Writing those entry points is the
> owners' work, not infrastructure work.

## 4. Data layout, naming and file formats

Git ignores `/output/`, `/cache/` and `.env` (see [`.gitignore`](../.gitignore)).
Downloaded data never enters Git today.

| Path (default) | Written by | Format | Granularity / naming |
|---|---|---|---|
| `cache/firms_hist/<site_id>/<YYYY-MM>.parquet` | `statusmap` | Parquet | one file per site and month |
| `output/<site_id>/firms.parquet` | `firms` | Parquet | one file per site, **whole requested window** |
| `output/<site_id>/<sensor>/<item_id>.tif` | `optical` | GeoTIFF (multi-band clip, ~90 kB at 20 m) | one file per scene, STAC item id |
| `output/<site_id>/<sensor>_manifest.parquet` | `optical` | Parquet | one file per site and sensor, **whole requested window** |
| `output/<site_id>/s5p_so2.parquet`, `s5p_orbits.parquet` | `s5p` | Parquet | one file per site, **whole requested window** |
| `output/<site_id>/era5/<YYYY-MM>.nc` | `era5` | netCDF | one file per site and month |
| `output/<site_id>/era5.parquet` | `era5` | Parquet | one file per site, whole requested window |
| `output/s5p_batch/<YYYY-MM>.parquet` | `s5p-batch` | Parquet | one file per month, all sites |
| `output/image_scores/<site>_<sensor>_<year>.parquet` | `image-scores` | Parquet | one file per site, sensor and year (clips are **not** stored) |
| `output/so2_copper/*.csv` | `so2-copper` | CSV | full recomputation each run |
| `output/status_map.html` | `statusmap` | self-contained HTML | full recomputation each run |

Metadata carried by every ingested record (module `ingest.pit`):
`acquired_at`, `available_at` (from the provider timestamp, or from a
conservative latency rule), `pit_basis`, and usually `ingested_at`. Optical
manifests also carry scene and AOI cloud, shadow and no-data fractions,
`processing_baseline`, `clip_path` and `error`. Image scores carry a
`quality` flag (`clear`/`partial`/`cloudy`/`nodata`/`missing`).

> **Finding A-4: the code does not separate RAW, PROCESSED and FINAL.** The
> report's architecture describes three storage areas, but the code writes
> everything under `output/` (and `cache/`). The split can be made through
> the existing `--out`/`--firms-dir`/`--data` arguments, with no code change
> (see [STORAGE.md](STORAGE.md)).

## 5. Idempotency, incremental behaviour, failure handling

| Command | Incremental? | Safe to rerun? | Writes atomic? | Exit code on partial failure |
|---|---|---|---|---|
| `statusmap` | **Yes**: skips months on disk; re-downloads the current month; a month with a failed request is not saved | Yes | No (direct `to_parquet`) | **0**: failures are printed and the command continues |
| `s5p-batch` | **Yes**: skips months on disk once `month end + 5 days` has passed; redoes unfinished months | Yes | **Yes** (`.tmp` then rename) | **0**: a month that fails is printed as `SKIPPED` and the loop goes on |
| `image-scores` | **Yes**: skips site/sensor/year files once `Jan 31 of the next year` has passed; redoes the current year completely | Yes | No | 0; a year with > 20 % failed scenes is not saved ("RETRY LATER") |
| `optical` | **Partly**: clips already on disk are not downloaded again | Clips yes; **the manifest is overwritten with only the requested window** | No | 0; per-scene errors are written in the manifest |
| `firms` | **No**: downloads the whole window again | Overwrites `firms.parquet` with only the requested window | No | non-zero if FIRMS keeps failing (exception) |
| `s5p` | **No** | Overwrites with only the requested window | No | non-zero on exception |
| `era5` | Monthly netCDF reused if present | **Risk:** a month downloaded while still incomplete is never refreshed | No (except `retrieve_overpass`, which uses `.part`) | non-zero on exception; `KeyError` if `CDSAPI_KEY` is missing |
| `so2-copper` | Full recomputation | Yes | No | non-zero on exception (for example with no S5P pixels at all) |

> **Finding A-5: exit code 0 on partial failure.** A Kubernetes Job reports
> **success** when `statusmap` or `s5p-batch` skipped months. Success in
> Kubernetes does not prove the data are complete. The logs must be checked,
> and a later run fills the gaps, because both commands resume.

> **Finding A-6: window-overwriting commands.** `firms`, `optical`
> (manifest), `s5p` and `era5` (table) replace their output with the window
> of the last call. Running them daily with a one-day window would erase the
> history in those files. They suit backfills and ad-hoc pulls, **not** the
> daily schedule. The daily schedule uses the month-partitioned,
> resumable commands (`statusmap`, `s5p-batch`, `image-scores`).

> **Finding A-7: no SIGTERM handling.** No command catches signals. A killed
> process can leave a half-written Parquet file for the commands without
> atomic writes. Only `s5p-batch` (and `era5.retrieve_overpass`) write to a
> temporary file and rename it.

Retries that already exist in the code:
- FIRMS: 8 retries with exponential backoff, plus a shared pause of up to
  15 min when the quota (5000 transactions / 10 min) is reached.
- Planetary Computer clips: 4 retries on 429/5xx.
- S5P catalogue searches: 6 tries; orbit reads: 4 tries; orbit files that
  return 404 are skipped.

## 6. Dependencies between jobs

```
smelter registry (data/registry, in Git)
   ├─ statusmap ──────────────► cache/firms_hist/*  ──► status_map.html
   ├─ s5p-batch ──────────────► s5p_batch/*  ─┐
   ├─ (no CLI) era5 point series ► era5_ts/* ─┴─► so2-copper ──► so2_copper/*.csv
   └─ image-scores ───────────► image_scores/*
```

- `statusmap`, `s5p-batch` and `image-scores` are **independent** of each
  other: separate sources, separate output files.
- `so2-copper` must run **after** `s5p-batch`, and needs the ERA5 point
  series to be present in the same `--data` directory.
- FIRMS has a key-wide quota. Two FIRMS-heavy jobs at the same time slow
  each other down.

## 7. Tests

- Runner: pytest, `testpaths = ["tests"]`. 11 test files, 51 test functions,
  **53 collected test cases** (some are parametrized).
- Result on 27 Nov 2026, Python 3.13, fresh virtualenv,
  `pip install -e ".[dev,era5,s5p,emissions]"`: **53 passed**, 0 failed,
  about 3 s. The 1345 warnings are mainly `shapely.ops.transform` deprecations.
- The tests need **no network and no credentials** (FIRMS availability is
  monkeypatched), so CI can run them in pull requests without secrets.
- The README says "24 unit tests" and the report says "42 tests". Both
  numbers are out of date.
- Python 3.10 (the declared minimum) was not tested in this audit. CI should
  test it.

## 8. External credentials and configuration

| Variable | Used in | Required for | Secret? |
|---|---|---|---|
| `FIRMS_MAP_KEY` | `ingest/firms.py` (`_key()`) | `firms`, `statusmap` (unless `--no-download`) | **Yes** |
| `CDSAPI_KEY` | `ingest/era5.py` | `era5` and the ERA5 helpers | **Yes** |
| `CDSAPI_URL` | `ingest/era5.py` | optional, defaults to `https://cds.climate.copernicus.eu/api` | No |
| `ORBITCU_DATA` | `scripts/trading_*.py` only | data root for the trading scripts | No |
| `HTTPS_PROXY`, `SSL_CERT_FILE` | honoured by `requests`; S5P reads pass `trust_env=True` | only behind a proxy | No |

Planetary Computer (Sentinel-2, Landsat, Sentinel-5P) is used **anonymously**
through `planetary_computer.sign_inplace`. The CDS ERA5 licence must be
accepted once on the CDS web site for the account behind `CDSAPI_KEY`.

Good practice already in the code: the FIRMS client never prints the request
URL, which contains the key (`ingest/firms.py`, end of `_get`).

## 9. Non-interactive execution

All commands run without a TTY or prompts. The only interactive feature is
`statusmap --open`, which opens a browser. It must not be used in a
container. Logs go to stdout/stderr: `print(..., flush=True)` in most
places, Python `logging` at WARNING level in `s5p-batch`. That suits
`kubectl logs`. `statusmap` shows progress with carriage returns (`\r`),
which makes long single lines in log collectors.

## 10. Risks and limitations (summary)

| # | Risk | Impact | Mitigation (planned) |
|---|---|---|---|
| R1 | Unpinned dependencies (A-1) | Images not reproducible; a new upstream version can break the pipeline | Constraints/lock file used by Docker and CI |
| R2 | Registry found via `__file__` (A-2) | A non-editable install in a container fails | Editable install inside the image, repository layout kept in `/app` |
| R3 | Exit code 0 on partial failure (A-5) | Kubernetes shows success with gaps | Log review; a wrapper script that greps `SKIPPED`/"could not be downloaded"; later, a small code change to return non-zero |
| R4 | Window-overwriting commands (A-6) | History lost if scheduled daily | Use only the resumable commands in the daily schedule |
| R5 | No ERA5 point-series command (A-3) | `so2-copper` cannot get fresh winds | Placeholder step; needs a CLI command from the SO2 owner |
| R6 | No atomic writes, no SIGTERM handling (A-7) | Corrupt file after a kill | `activeDeadlineSeconds` generous; restore from durable copy; later, atomic writes |
| R7 | `image-scores` redoes the whole current year on every run | Long daily runtime | Weekly schedule instead of daily |
| R8 | FIRMS quota shared by key | Parallel jobs slow each other | One FIRMS job at a time (`concurrencyPolicy: Forbid`, one pipeline) |
| R9 | Data only on laptops today | Loss of a laptop loses downloads | Durable object storage (see [STORAGE.md](STORAGE.md)) |
| R10 | Analysis outputs (`activity_index/…`) are produced outside the CLI | Cannot be reproduced automatically | Out of infrastructure scope; listed in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) |

## 11. Files to create or modify in the next phases

The proposed designs are described in the documents listed in the last column.

| File | Action | Phase | Described in |
|---|---|---|---|
| `Dockerfile`, `.dockerignore` | create | 3 | [DOCKER.md](DOCKER.md) |
| `constraints.txt` (or lock file) | create | 3 | [DOCKER.md](DOCKER.md) |
| `infra/scripts/daily_pipeline.sh` | create | 3/8 | [OPERATIONS.md](OPERATIONS.md) |
| `.github/workflows/ci.yml` | create | 4 | [CI_CD.md](CI_CD.md) |
| `.github/workflows/docker-publish.yml` | create | 5 | [CI_CD.md](CI_CD.md) |
| `infra/kubernetes/*.yaml`, `kustomization.yaml` | create | 7 | [KUBERNETES.md](KUBERNETES.md) |
| `.env.example`, `.gitignore` (add `*.env`, `secrets/`) | create / modify | 9 | [SECURITY.md](SECURITY.md) |
| `README.md` | modified in this phase | 2 | — |
| `src/orbitcu/*` | **no change planned** by infrastructure; small optional changes (exit codes, ERA5 CLI) are proposed to the module owners | — | [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) |
