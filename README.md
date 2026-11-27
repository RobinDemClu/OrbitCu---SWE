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

Infrastructure documentation (Docker, Kubernetes, daily automation, storage,
CI/CD) is in [`docs/`](docs/), see [Infrastructure](#infrastructure) below.

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
docs/*.md           infrastructure documentation (Docker, Kubernetes, storage, CI/CD)
```

## Quick start

```bash
pip install -e ".[dev,era5,s5p,emissions]"
export FIRMS_MAP_KEY=...      # Windows PowerShell: $env:FIRMS_MAP_KEY = "..."
export CDSAPI_KEY=...         # Windows PowerShell: $env:CDSAPI_KEY = "..."
pytest                        # 53 unit tests, no network (needs the extras above)
orbitcu statusmap --open      # interactive smelter status map
```

All commands and options are described in the report, section "Using the code".

## Infrastructure

> **Status (27 November 2026): designed and documented, not implemented.**
> Runs are still started by hand with the `orbitcu` CLI. The repository has
> no `Dockerfile`, no GitHub Actions workflow and no Kubernetes manifest yet.
> See [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) for
> what exists, what was tested and what remains.

| Topic | Planned design | Document |
|---|---|---|
| Audit of the current code | Commands, data layout, idempotency, risks | [`docs/INFRASTRUCTURE_AUDIT.md`](docs/INFRASTRUCTURE_AUDIT.md) |
| Architecture | GitHub → GHCR → Kubernetes CronJob → Azure Blob | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Docker | `python:3.12-slim`, non-root, editable install, `/work` volume | [`docs/DOCKER.md`](docs/DOCKER.md) |
| CI/CD | `ci.yml` (pytest 3.10/3.12, Docker build, manifest checks), `docker-publish.yml` (GHCR) | [`docs/CI_CD.md`](docs/CI_CD.md) |
| Container image | `ghcr.io/robindemclu/orbitcu:<commit-sha>` (not published yet) | [`docs/CI_CD.md`](docs/CI_CD.md) |
| Kubernetes and daily schedule | CronJob `orbitcu-daily` 06:30 UTC (`concurrencyPolicy: Forbid`), upload 12:00 UTC, manual backfill Job | [`docs/KUBERNETES.md`](docs/KUBERNETES.md) |
| Storage | Durable Azure Blob copy (copy-only, soft delete, versioning) and a PVC working copy | [`docs/STORAGE.md`](docs/STORAGE.md) |
| Secrets | Kubernetes Secrets from git-ignored local files; no keys in CI or images | [`docs/SECURITY.md`](docs/SECURITY.md) |
| Local set-up | Python, Docker, Kind | [`docs/ENVIRONMENT_SETUP.md`](docs/ENVIRONMENT_SETUP.md) |
| Operations | Daily script, runbooks, validation plan, troubleshooting | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |

Planned usage once the files are added (not available yet):

```bash
docker build -t orbitcu:dev .
docker run --rm --init --env-file .env -v "$PWD/work:/work" orbitcu:dev statusmap --months 3
kubectl apply -k infra/kubernetes
kubectl -n orbitcu create job --from=cronjob/orbitcu-daily orbitcu-daily-manual-1
```

Troubleshooting for common errors (missing keys, registry not found, scipy
missing for the tests, FIRMS quota) is in
[`docs/OPERATIONS.md`](docs/OPERATIONS.md#7-troubleshooting).

## Team

Baptiste, Radu, Ethan, Robin.
