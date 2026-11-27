# OrbitCu — Docker

> **Status: planned, not implemented.** The repository has **no `Dockerfile`
> and no `.dockerignore`** yet. This document is the specification for Phase 3.
> The commands under "Planned usage" will only work once those files exist.
> Commands marked *works today* can be run now.

## 1. Requirements from the audit

| Requirement | Source | Design answer |
|---|---|---|
| Python ≥ 3.10 | `pyproject.toml` | `python:3.12-slim` base, pinned by digest in the real Dockerfile |
| Registry CSVs found via `Path(__file__).parents[2]` | `src/orbitcu/sites.py` | Keep the repository layout in `/app` and use an **editable** install (`pip install -e`) |
| Native geo libraries (GDAL, PROJ, HDF5) | rasterio, pyproj, h5py, netcdf4 | Use the manylinux wheels; no `apt-get install` needed on amd64 |
| All extras needed by the scheduled commands | `s5p-batch` (s5p), `so2-copper` (emissions), ERA5 (era5) | Install `.[era5,s5p,emissions]`; `dev` only in the test stage |
| Unpinned dependencies | audit A-1 | `constraints.txt` generated once (`pip freeze`) and committed; installed with `-c constraints.txt` |
| Relative default paths (`output/`, `cache/`) | `cli.py`, `statusmap.py` | `WORKDIR /data`, and the pipeline always passes absolute paths |
| No credentials in the image | security | Keys only as runtime environment variables (Kubernetes Secret, or `--env-file` locally) |

## 2. Planned `Dockerfile` (specification)

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --gid 10001 orbitcu && useradd --uid 10001 --gid orbitcu --home-dir /app --shell /usr/sbin/nologin orbitcu
WORKDIR /app

# dependencies first (layer cache): only pyproject + constraints
COPY pyproject.toml constraints.txt ./
COPY src/orbitcu/__init__.py src/orbitcu/__init__.py
RUN pip install -c constraints.txt -e ".[era5,s5p,emissions]"

# application code and the small reference data that the code reads from the repo tree
COPY src/ src/
COPY data/registry/ data/registry/
COPY data/reference/ data/reference/
COPY infra/scripts/ infra/scripts/
RUN pip install --no-deps -e . && mkdir -p /data && chown orbitcu:orbitcu /data

USER 10001:10001
WORKDIR /data
ENTRYPOINT ["orbitcu"]
CMD ["--help"]

# test stage, used by CI only: docker build --target test .
FROM base AS test
USER root
RUN pip install -c /app/constraints.txt pytest
COPY tests/ /app/tests/
USER 10001:10001
WORKDIR /app
CMD ["python", "-m", "pytest", "-q"]
```

Notes:
- `ENTRYPOINT ["orbitcu"]` keeps the existing CLI. The pipeline script is
  started with `--entrypoint /app/infra/scripts/daily_pipeline.sh` or with
  `command:` in Kubernetes.
- **SIGTERM:** with the exec-form entrypoint, `orbitcu` is PID 1 and receives
  SIGTERM directly. Python's default SIGTERM action ends the process at once,
  so the code gets no cleanup chance (audit A-7). The planned shell wrapper
  uses `exec` for single commands. Kubernetes `terminationGracePeriodSeconds`
  only matters once the code handles the signal, which is a possible later
  improvement. Months that are not finished are not marked done, so the next
  run redoes them. The remaining risk is a half-written Parquet file in the
  commands without atomic writes. See [OPERATIONS.md](OPERATIONS.md#5-troubleshooting).
- `data/trading/`, `docs/`, `scripts/` and `tests/` are **not** in the runtime image.
- On arm64 (Apple Silicon), check that wheels exist for all geo packages
  before building. CI builds `linux/amd64` only.

## 3. Planned `.dockerignore`

```
.git
.github
**/__pycache__
**/*.pyc
*.egg-info
.pytest_cache
.venv
venv
.env
*.env
secrets/
output/
cache/
data/trading/
docs/
*.tif
*.nc
*.parquet
```

`*.parquet`, `*.tif` and `*.nc` are excluded so that downloaded satellite
data can never enter an image layer, even if someone keeps it inside the
repository folder.

## 4. Planned usage

Build (from the repository root):

```bash
docker build -t orbitcu:dev .
```

Run the existing test suite inside the image:

```bash
docker build --target test -t orbitcu:test .
```

```bash
docker run --rm orbitcu:test
```

Check that the CLI responds:

```bash
docker run --rm orbitcu:dev --help
```

```bash
docker run --rm orbitcu:dev statusmap --help
```

Small real download, with a host folder as storage and keys from a local env
file that is never committed (see [ENVIRONMENT_SETUP.md](ENVIRONMENT_SETUP.md)):

```bash
mkdir -p ./local-data
docker run --rm --env-file .env -v "$PWD/local-data:/data" orbitcu:dev \
  statusmap --months 1 --firms-dir /data/raw/firms_hist --out /data/final/status_map.html --workers 2
```

Run it a second time: it prints `VIIRS history: …` with only the current
month to download. That shows the incremental behaviour.

S5P (no key needed, Planetary Computer is anonymous):

```bash
docker run --rm -v "$PWD/local-data:/data" orbitcu:dev \
  s5p-batch --sites PL-GLOGOW --start 2026-10 --end 2026-10 --out /data/raw --workers 2
```

On Linux, the host folder must be writable by UID 10001. Use
`sudo chown -R 10001:10001 local-data`, or add `--user "$(id -u):$(id -g)"`.

## 5. Image naming

| Tag | When | Example |
|---|---|---|
| `sha-<7-char sha>` | every publish (immutable, used by Kubernetes) | `ghcr.io/robindemclu/orbitcu:sha-a7b3916` |
| `developpement`, `main` | moving pointer to the branch head | `ghcr.io/robindemclu/orbitcu:developpement` |
| `vX.Y.Z`, `X.Y` | Git tag `vX.Y.Z` | `ghcr.io/robindemclu/orbitcu:v0.1.0` |
| `latest` | latest `main` only; never used by the manifests | — |

The GHCR owner name is lowercase (`robindemclu`), because image names must
be lowercase. Publishing is described in [CI_CD.md](CI_CD.md).

## 6. Works today (without Docker)

```bash
pip install -e ".[dev,era5,s5p,emissions]"
pytest -q
orbitcu --help
```

This is the same as what the image will contain, and it is the fastest way
to reproduce a container problem on a laptop.

## 7. Validation checklist for Phase 3

- [ ] `docker build .` succeeds on a clean checkout (CI).
- [ ] `docker build --target test .` then `docker run` → 53 tests pass.
- [ ] `docker run --rm orbitcu:dev --help` lists `firms optical s5p era5 s5p-batch`.
- [ ] `docker run --rm orbitcu:dev so2-copper --help` and `image-scores --help` work (the dispatched commands).
- [ ] `docker run --rm --entrypoint id orbitcu:dev` → `uid=10001`.
- [ ] `docker history orbitcu:dev` shows no `.env`, key or data file.
- [ ] Image size recorded in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).
