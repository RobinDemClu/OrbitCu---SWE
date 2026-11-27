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
scripts/            stand-alone trading analysis scripts
docs/latex/         project documentation (LaTeX + PDF)
docs/*.md           infrastructure documentation (Docker, Kubernetes, storage, CI/CD)
```

## Quick start

```bash
pip install -e ".[dev,era5,s5p,emissions]"
export FIRMS_MAP_KEY=...      # Windows PowerShell: $env:FIRMS_MAP_KEY = "..."
export CDSAPI_KEY=...         # Windows PowerShell: $env:CDSAPI_KEY = "..."
pytest                        # 53 unit tests, no network
orbitcu statusmap --open      # interactive smelter status map
```

All commands and options are described in the report, section "Using the code".

## Infrastructure and automation

> **Status (27 November 2026): design documented, not implemented.** The
> repository has no `Dockerfile`, no GitHub Actions workflow and no Kubernetes
> manifests yet. Every command is still started by hand. Data live on local
> disks under `output/` and `cache/`, which Git ignores. There is no shared
> storage and no backup.

The planned setup:
- GitHub Actions runs the tests and builds a Docker image.
- The image is published to GHCR as `ghcr.io/robindemclu/orbitcu:sha-<commit>`.
- A Kubernetes CronJob runs a daily pipeline of existing `orbitcu` commands
  at 04:00 UTC: `statusmap`, then `s5p-batch`, then `so2-copper`.
- A weekly CronJob runs `image-scores`.
- Data are kept on a PersistentVolumeClaim and copied, never deleted, to
  durable object storage (Azure Blob) in `raw/`, `processed/` and `final/`.
- Credentials stay in Kubernetes Secrets.

| Document | Content |
|---|---|
| [INFRASTRUCTURE_AUDIT.md](docs/INFRASTRUCTURE_AUDIT.md) | What exists: commands, data layout, idempotency, credentials, risks |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Target architecture and design decisions (CronJob vs Argo, storage choice) |
| [DOCKER.md](docs/DOCKER.md) | Container image specification and usage |
| [KUBERNETES.md](docs/KUBERNETES.md) | Manifests, CronJobs, schedules, resources, deployment commands |
| [STORAGE.md](docs/STORAGE.md) | Persistent storage, layout, sync, retention, recovery, validation, costs |
| [CI_CD.md](docs/CI_CD.md) | GitHub Actions CI and GHCR publishing |
| [ENVIRONMENT_SETUP.md](docs/ENVIRONMENT_SETUP.md) | Local setup, credentials, tools |
| [OPERATIONS.md](docs/OPERATIONS.md) | Daily pipeline, backfills, runbooks, troubleshooting |
| [SECURITY.md](docs/SECURITY.md) | Secrets and hardening |
| [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) | What is done, documented, blocked or not started |

Commands that can be rerun safely and only download what is missing today:
`orbitcu statusmap`, `orbitcu s5p-batch`, `orbitcu image-scores`. The other
download commands overwrite their output with the requested date window. Use
them for one-off pulls (see [OPERATIONS.md](docs/OPERATIONS.md)).

## Team

Baptiste, Radu, Ethan, Robin.
