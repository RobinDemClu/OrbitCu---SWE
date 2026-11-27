# OrbitCu — Infrastructure Audit (Phase 1)

> **Audit date:** 27 November 2026
> **Audited revision:** branch `developpement`, commit `da24cb4`
> (`main` is at `0f90bc4`, 21 commits behind `developpement`, 0 ahead).
> **Scope:** infrastructure, packaging, automation, storage and CI. The
> scientific algorithms, copper modelling and trading code were read only to
> understand their inputs and outputs. They were **not** reviewed or changed.

This report records the state of the repository **as it is**. Nothing here
describes infrastructure that already exists unless it says so. For the
proposed design see [ARCHITECTURE.md](ARCHITECTURE.md), and for progress see
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

---

## 1. Repository structure

```
.gitattributes            text=auto line endings
.gitignore                __pycache__/, *.egg-info/, .pytest_cache/, /output/, /cache/, .env
README.md
pyproject.toml            setuptools build, package "orbitcu" 0.1.0, Python >= 3.10
data/
  registry/               smelter_registry.csv, smelter_sites.csv (29 sites), smelter_excluded.csv,
                          site_priors.csv, c_site_activity_check.csv
  reference/              known_events.csv, official_stats.csv, reported_output.csv
  trading/                ~30 CSV/JSON result tables of the trading studies (committed)
docs/latex/               orbitcu_report.tex + .pdf (main project document), figures/
scripts/                  11 trading_*.py analysis scripts (not part of the CLI)
src/orbitcu/              Python package
  cli.py                  `orbitcu` entry point
  ingest/                 firms.py, optical.py, s5p.py, s5p_batch.py, era5.py, pit.py
  sites.py, geolocate.py, activity_check.py, statusmap.py, hotspot.py, image_scores.py,
  emissions.py, so2_versions.py, so2_copper.py, copper.py, production.py,
  activity_index.py, trading.py
  templates/              status_map.html, countries-50m.json (+ LICENSE)
tests/                    13 test modules, 53 tests
```

**Not present:** `Dockerfile`, `.dockerignore`, `.github/` (no GitHub Actions
workflows), `infra/`, Kubernetes manifests, Argo workflows, Helm charts,
`.env.example`, requirements lock file, `__main__.py`.

The size of the tracked content is small (about 0.5 MB under `data/`, 4.8 MB
under `docs/latex/`). No satellite imagery is tracked in Git. Downloads go to
`/output/` and `/cache/`, which are git-ignored.

## 2. Current architecture

The project report (`docs/latex/orbitcu_report.tex`, section *Software
architecture*) describes OrbitCu as **independent jobs** that exchange data
through three storage areas, **RAW**, **PROCESSED** and **FINAL**. It plans
orchestration as *"one container per job, run every day by Kubernetes + Argo
(planned) · today: started by hand with the `orbitcu` command · tests: pytest
on GitHub"*.

What the code actually does today:

| Concept in the report | Implementation today |
|---|---|
| Jobs | Sub-commands of the `orbitcu` CLI, started by hand on team laptops (Windows 11) |
| RAW store | Local folders: `output/<site>/...`, `output/s5p_batch/`, `cache/firms_hist/` |
| PROCESSED store | Local folders: `output/image_scores/` |
| FINAL store | Local files: `output/status_map.html`, `output/so2_copper/*.csv` |
| Orchestration | None. Runs are manual. |
| Tests on GitHub | **Not implemented.** The repository has no workflows. |
| Containers | **Not implemented.** |

All paths are **relative to the current working directory**. They can be
overridden per command with `--out`, `--firms-dir` or `--data`. There is no
global storage setting and no environment variable for the data location in
the package. The `scripts/trading_*.py` files read `ORBITCU_DATA`, which
defaults to `/mnt/project-files/data/`.

## 3. Python package and dependencies

From `pyproject.toml`:

| Group | Packages |
|---|---|
| core | numpy, pandas, pyarrow, requests, pystac-client, planetary-computer, rasterio, shapely, pyproj |
| `era5` | cdsapi>=0.7, xarray, netcdf4 |
| `s5p` | xarray, h5netcdf, h5py, fsspec, aiohttp |
| `emissions` | scipy, xarray, netcdf4 |
| `dev` | pytest |

* Build backend: setuptools>=68, `src/` layout, package data `templates/*`.
* No versions are pinned and there is no lock file, so builds are **not
  reproducible**. Two installs on different days can resolve different
  versions.
* `scripts/` also import scipy (`spearmanr`, `norm`, ...), which is covered by
  the `emissions` extra.
* Installation documented in the README:
  `pip install -e ".[dev,era5,s5p,emissions]"`.

### Packaging finding (important for Docker)

`src/orbitcu/sites.py` defines:

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = REPO_ROOT / "data" / "registry"
SITES_CSV = REGISTRY_DIR / "smelter_sites.csv"
```

and `so2_copper.py` reads `REPO_ROOT / "data" / "reference"`. The package
only finds the smelter registry when it runs **from a source checkout**, which
means an editable install (`pip install -e .`) or `PYTHONPATH=src`. A normal
`pip install .` copies the package into `site-packages`. `REPO_ROOT` then
points at the wrong directory and every command fails with
`FileNotFoundError` on `smelter_sites.csv`. The container design therefore has
to copy the repository and use an editable install (see [DOCKER.md](DOCKER.md)).

## 4. CLI commands (verified with `--help`)

Entry point: `orbitcu = "orbitcu.cli:main"`. `cli.main` sends `so2-copper`,
`image-scores` and `statusmap` to their own modules. The other five commands
use a shared argparse parser. All five take `--sites [ids...]` (default: all
29), `--start`, `--end` and `--out` (default `output`).

| Command | Extra options | Writes | Network / credentials |
|---|---|---|---|
| `orbitcu firms` | `--wide` | `<out>/<site>/firms.parquet` | NASA FIRMS, `FIRMS_MAP_KEY` |
| `orbitcu optical --sensor {sentinel2,landsat}` | `--max-cloud` | `<out>/<site>/<sensor>/<item_id>.tif` (GeoTIFF clips), `<out>/<site>/<sensor>_manifest.parquet` | Microsoft Planetary Computer STAC + data API (anonymous) |
| `orbitcu s5p` | `--modes {OFFL,NRTI,RPRO}` (default OFFL) | `<out>/<site>/s5p_so2.parquet`, `<out>/<site>/s5p_orbits.parquet` | Planetary Computer (anonymous) |
| `orbitcu s5p-batch` | `--workers` (8), `--reverse`, `--modes` (default OFFL RPRO NRTI); `--start/--end` are months `YYYY-MM` | `<out>/s5p_batch/<YYYY-MM>.parquet` | Planetary Computer (anonymous) |
| `orbitcu era5` | – | `<out>/<site>/era5/<YYYY-MM>.nc`, `<out>/<site>/era5.parquet` | Copernicus CDS, `CDSAPI_KEY` (`CDSAPI_URL` optional) |
| `orbitcu statusmap` | `--months` (12), `--start/--end YYYY-MM`, `--firms-dir` (`cache/firms_hist`), `--radius-km`, `--out` (`output/status_map.html`), `--json`, `--refresh`, `--no-download`, `--workers` (6), `--open` | `cache/firms_hist/<site>/<YYYY-MM>.parquet`, `output/status_map.html` | NASA FIRMS, `FIRMS_MAP_KEY` (unless `--no-download`) |
| `orbitcu image-scores` | `--sites`, `--sensors`, `--start-year` (2018), `--end-year` (2026), `--out` (`output/image_scores`), `--workers` (16) | `<out>/<site>_<sensor>_<year>.parquet`, `image_scores_all.csv`, `image_scores_monthly.csv` | Planetary Computer (anonymous). Clips are **not** stored. |
| `orbitcu so2-copper` | `--data` (`output`), `--out` (`output/so2_copper`), `--sites`, `--start`, `--end`, `--tau`, `--sigma`, `--radius` | CSV results under `--out` | None (reads local files) |

Other observations:

* There is no `python -m orbitcu`. `python -m orbitcu.cli` works.
* `statusmap --open` starts a web browser and is not suitable for containers.
  Every other option runs without user input.
* Several analysis steps (`production`, `activity_index`, `trading`,
  `copper`) are library modules or `scripts/` and **have no CLI command**.
  The daily pipeline cannot call them through `orbitcu`.
* **Gap:** `so2-copper` reads `<data>/era5_ts/*.parquet`. These files come
  from `ingest.era5.retrieve_timeseries()`, which **no CLI command calls**.
  `orbitcu era5` writes a different layout (`<site>/era5/*.nc`). So
  `so2-copper` cannot be fed end to end with CLI commands only.

## 5. Idempotency, incremental downloads and file naming

| Command | Incremental? | Safe to re-run? | Notes / risks |
|---|---|---|---|
| `statusmap` (FIRMS cache) | **Yes.** Months already on disk are skipped; the current month is always downloaded again. | Yes | An incomplete month (failed request) is not written. The month files are written directly, not through a temporary file, so a kill during `to_parquet` can leave a corrupt file that later runs would skip. Exit code is 0 even when some site-months failed (they are only printed). |
| `s5p-batch` | **Yes.** A finished month (`month end + 5 days` in the past) is skipped; recent months are redone. | Yes | Writes through `*.tmp` and then renames (atomic). A month with failed orbits raises an error, but `cmd_s5p_batch` catches it and prints `SKIPPED`, so **exit code 0**. |
| `image-scores` | **Yes.** A year file is skipped once `year+1-01-31` has passed. | Yes | The **current year is scored again on every run** (the whole year of scenes is fetched again). Years with more than 20 % failed scenes are not saved (`RETRY LATER`), with exit code 0. |
| `optical` | Partly. Clips (`<item_id>.tif`) are skipped if they exist. | Clips: yes. Manifest: **no** | `<sensor>_manifest.parquet` is **overwritten** with the scenes of the requested window only. A daily run with a short window would drop older rows from the manifest (the clips stay on disk). |
| `firms` | No | Overwrites | `<site>/firms.parquet` is rewritten with the requested window. Not suited to daily accumulation; use the `statusmap` cache instead. |
| `s5p` | No | Overwrites | Per-site files are rewritten. `s5p-batch` is the incremental path. |
| `era5` | Monthly `.nc` files are skipped if they exist | **Risky** | A month downloaded while it is still in progress is **never refreshed**, because `retrieve` skips any existing file. `era5.parquet` is overwritten with the requested window. |
| `so2-copper` | Recomputes everything | Yes | Pure function of the local inputs. |

**Retries already in the code:** FIRMS `_get` retries up to 8 times and pauses
all threads globally when the quota is hit. Optical `fetch_clip` retries 4
times. S5P orbit reads retry 4 times with linear backoff. Network errors in
`s5p-batch`, `statusmap` and `image-scores` are recorded so that the next run
fills the gap.

**No-look-ahead metadata already in the data:** `ingest/pit.py` gives every
record `acquired_at` and `available_at` (provider timestamp where available,
otherwise a latency rule: FIRMS NRT 3 h, S5P NRTI 3 h, S5P OFFL 5 d, ERA5T 5 d,
Sentinel-2 L2A 1 d, Landsat C2 L2 16 d) plus `pit_basis`. Optical manifests
also keep `scene_cloud_pct`, cloud and shadow fractions, `processing_baseline`,
`clip_path`, `error` and `ingested_at`. FIRMS rows keep `ingested_at`. The
infrastructure must keep these files byte for byte and must not rewrite
timestamps.

## 6. Tests

* 13 modules in `tests/`, **53 tests**. The README says "24 unit tests" and
  the report says "42 tests". Both are out of date.
* Run with `pytest` (`testpaths = ["tests"]`). No network is needed.
* **Result on this audit:** `53 passed, 6 warnings` (Python 3.13.18,
  installed with `pip install -e ".[dev,era5,s5p,emissions]"`).
* **Finding:** `pip install -e ".[dev]"` alone is not enough.
  `tests/test_emissions.py` fails at collection with
  `ModuleNotFoundError: No module named 'scipy'`. CI must install the
  `emissions` extra, or simply all extras as the README says.
* Warnings: a Pydantic V2 deprecation in `planetary_computer` and a
  `shapely.ops.transform` deprecation in `sites.py`. They are harmless now,
  but would become failures with future major versions if dependencies stay
  unpinned.
* Tests have never run on Python 3.10 in CI, although 3.10 is the declared
  minimum.

## 7. External credentials

| Variable | Used by | Required? | How to obtain |
|---|---|---|---|
| `FIRMS_MAP_KEY` | `ingest/firms.py` (`firms`, `statusmap`) | Yes for those commands | Free, https://firms.modaps.eosdis.nasa.gov/api/map_key/ (5000 transactions / 10 min) |
| `CDSAPI_KEY` | `ingest/era5.py` (`era5`) | Yes for `era5` | Copernicus CDS account; the ERA5 licence must be accepted once on the website |
| `CDSAPI_URL` | `ingest/era5.py` | Optional (default `https://cds.climate.copernicus.eu/api`) | – |
| `PC_SDK_SUBSCRIPTION_KEY` | `planetary-computer` library (not referenced in OrbitCu code) | Optional; anonymous access works | Planetary Computer account |
| `ORBITCU_DATA` | `scripts/trading_*.py` | Only for the scripts (not secret) | – |
| Storage credentials (Azure Blob SAS / key) | **Planned** infrastructure only | – | See [SECURITY.md](SECURITY.md) |

Current handling: keys are exported in the shell. `.env` is git-ignored. The
code never prints the keys, but `firms.transactions_used()` builds a URL
containing the key, so any future error log that prints the full URL would
leak it.

## 8. Which commands can run non-interactively in a container

All of them except `statusmap --open`. None prompts for input. The concerns
in a container are:

1. Writable working directory: relative default paths (`output/`, `cache/`).
2. The registry must be reachable through `REPO_ROOT` (see §3).
3. The **exit code** does not always reflect partial failure (`s5p-batch`,
   `statusmap`, `image-scores` print and continue). Kubernetes would mark such
   a run "Succeeded", so the logs have to be checked.
4. SIGTERM: Python's default handler stops the process immediately.
   `s5p-batch` is safe thanks to its temporary-file-and-rename. Direct
   `to_parquet` writes (statusmap months, image-score years, manifests) can be
   cut off midway.

## 9. Job dependencies (what can run in parallel)

```
smelter registry (data/registry, in Git)
   │
   ├── statusmap  (FIRMS → cache/firms_hist → status_map.html)      independent
   ├── s5p-batch  (S5P → output/s5p_batch)  ─┐
   │                                          ├─► so2-copper (needs era5_ts: GAP)
   ├── era5 / era5_ts (CDS)  ────────────────┘
   ├── image-scores (Planetary Computer → output/image_scores)       independent
   └── optical (clips + manifests)                                   independent
```

* `statusmap`, `s5p-batch`, `image-scores` and `optical` share no outputs and
  could run in parallel. They do share outbound bandwidth, and FIRMS has a
  per-key quota (`statusmap` and `firms` should not run at the same time with
  the same key).
* `so2-copper` must run **after** `s5p-batch` and the ERA5 time series.
* No step should run twice at the same time on the same storage. The code
  has no locking.

## 10. Risks and limitations

| # | Risk | Impact | Mitigation (proposed) |
|---|---|---|---|
| R1 | Registry located through `__file__` (`REPO_ROOT`) | Non-editable installs break | Editable install inside the image (no code change) |
| R2 | Unpinned dependencies | Non-reproducible builds, future breakage | Constraints file generated in CI; pin the base image |
| R3 | Partial failures return exit code 0 | Silent gaps in a scheduled run | Pipeline wrapper scans logs for `SKIPPED`/`RETRY LATER`/`could not be downloaded` and fails the job |
| R4 | Non-atomic Parquet writes | A corrupt file could be skipped forever | `activeDeadlineSeconds` with margin; validation step that opens the latest files; later, a small code change to write through tmp+rename |
| R5 | `optical`/`firms`/`s5p` overwrite per-window files | History loss if run daily with short windows | Do not use them in the daily job; use `statusmap`, `s5p-batch`, `image-scores` |
| R6 | ERA5 in-progress month never refreshed | Stale wind data | Only request complete months (ERA5T + 5 d), or delete the current-month file before a refresh |
| R7 | `era5_ts` not produced by the CLI | `so2-copper` cannot be automated | Add a small `orbitcu era5-ts` command (needs team approval: it is a code change) |
| R8 | No locking | Two concurrent runs corrupt the cache | `concurrencyPolicy: Forbid` and a single writer |
| R9 | `image-scores` rescans the whole current year every run | Daily cost grows through the year | Run it weekly, or limit `--sensors`/`--sites` |
| R10 | FIRMS quota 5000 / 10 min | Long backfills throttled | Already handled by the global backoff; keep `--workers` low |
| R11 | No CI | Regressions reach `main` unnoticed | `ci.yml` (see [CI_CD.md](CI_CD.md)) |
| R12 | Data only on laptops | Loss of a laptop loses the downloads | Durable object storage (see [STORAGE.md](STORAGE.md)) |

## 11. Proposed infrastructure architecture (summary)

Details in [ARCHITECTURE.md](ARCHITECTURE.md).

* **Source:** GitHub, with `developpement` as the integration branch and
  `main` as the stable branch.
* **CI:** GitHub Actions, using pytest on 3.10 and 3.12, a Docker build and a
  manifest validation.
* **Images:** GHCR, `ghcr.io/robindemclu/orbitcu:<git-sha>`, plus semver tags
  on releases.
* **Runtime:** one Kubernetes **CronJob**, once a day, running a sequential
  pipeline script that calls the existing CLI commands. Argo is postponed:
  the useful parallelism is small and Argo adds a controller to install and
  operate.
* **Working storage:** a PVC mounted at `/work` holding the `output/` and
  `cache/` trees the CLI already uses.
* **Durable storage:** Azure Blob Storage (Azure for Students), synchronised
  with **copy-only** semantics (never delete), plus blob soft-delete and
  versioning. An S3-compatible alternative is documented.
* **Secrets:** Kubernetes Secret created by hand from GitHub-free local
  files. GitHub Secrets are used only for CI.
* **Local validation:** Kind.

## 12. Implementation plan

| Step | Deliverable | Code change to the app? |
|---|---|---|
| 1 | Documentation set (this phase) | No |
| 2 | `Dockerfile`, `.dockerignore`, `.env.example`, `.gitignore` additions | No |
| 3 | `.github/workflows/ci.yml` | No |
| 4 | `.github/workflows/docker-publish.yml` | No |
| 5 | `infra/kubernetes/` (namespace, configmap, PVC, CronJob, ServiceAccount, kustomization) and `infra/scripts/daily.sh` | No |
| 6 | Blob sync step (rclone sidecar/init or post-step) and validation runbook | No |
| 7 | Validation on Kind (Phase 10 checklist in [OPERATIONS.md](OPERATIONS.md)) | No |
| 8 | Optional, needs team approval: `orbitcu era5-ts` command; atomic writes in `statusmap`/`image_scores`; non-zero exit on partial failure | **Yes (small)** |

## 13. Files that will be created or modified

Created: `Dockerfile`, `.dockerignore`, `.env.example`,
`.github/workflows/ci.yml`, `.github/workflows/docker-publish.yml`,
`infra/kubernetes/{namespace,serviceaccount,configmap,storage,cronjob,kustomization}.yaml`,
`infra/kubernetes/secret.example.yaml` (placeholders only),
`infra/scripts/daily.sh`, and the `docs/*.md` documents in this set.

Modified: `README.md` (infrastructure section, test count) and `.gitignore`
(local secret files, `*.tmp`, kubeconfig).

Not modified: everything under `src/`, `tests/`, `scripts/`, `data/` and
`docs/latex/`.
