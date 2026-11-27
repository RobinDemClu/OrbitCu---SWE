# OrbitCu — Infrastructure Architecture

> **Status:** design document. The application (the `orbitcu` CLI and its
> modules) **exists**. The infrastructure described here (containers, CI,
> Kubernetes, cloud storage) is **planned and not implemented yet**, unless
> [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) says otherwise.
> Facts about the current code come from
> [INFRASTRUCTURE_AUDIT.md](INFRASTRUCTURE_AUDIT.md).

## 1. Goals and constraints

* Run the existing satellite downloads and processing **once a day, without a
  person**, and keep every downloaded file.
* **Cost:** at or near \$0. The project has no budget, so the design avoids
  managed Kubernetes fees and paid services (the report states "Cost: \$0",
  with Azure for Students as optional credit).
* **Understandable by students:** plain Kubernetes objects and Kustomize. No
  Helm and no service mesh.
* **No application rewrite:** the infrastructure calls the CLI exactly as a
  person would.
* **Portable:** the design works the same on Kind (laptop), on a single VM
  running k3s, or on a managed cluster (AKS) later.

## 2. Overview

```
 ┌──────────── GitHub ────────────┐
 │ repo RobinDemClu/OrbitCu---SWE │
 │  developpement ──PR──► main    │
 │        │                       │
 │  GitHub Actions                │
 │   ci.yml: pytest, docker build,│
 │           manifest validation  │
 │   docker-publish.yml ──────────┼──► GHCR  ghcr.io/robindemclu/orbitcu:<sha>
 └────────────────────────────────┘                         │ image pull
                                                            ▼
 ┌──────────────── Kubernetes cluster (Kind locally / k3s VM / AKS later) ──────────────┐
 │ namespace: orbitcu                                                                   │
 │                                                                                      │
 │  CronJob orbitcu-daily  (06:30 UTC, concurrencyPolicy: Forbid)                       │
 │   └─ Job ─ Pod                                                                       │
 │        initContainer  restore  : rclone copy  blob → /work   (only missing files)    │
 │        container      pipeline : infra/scripts/daily.sh → orbitcu statusmap,        │
 │                                  s5p-batch, image-scores ...  (sequential)           │
 │        container      (step)   : rclone copy  /work → blob   (never deletes)         │
 │                                                                                      │
 │  PVC orbitcu-work (/work)  ← working copy of output/ and cache/                      │
 │  Secret orbitcu-credentials (FIRMS_MAP_KEY, CDSAPI_KEY)                              │
 │  Secret orbitcu-storage      (rclone remote config / SAS)                            │
 │  ConfigMap orbitcu-config    (paths, windows, workers)                               │
 └──────────────────────────────────────────────────────────────────────────────────────┘
                                                            │ HTTPS
                                                            ▼
                         Azure Blob Storage  (container "orbitcu")
                         raw/  processed/  final/   soft delete + versioning
```

External data providers (accessed over HTTPS from the pod): NASA FIRMS,
Microsoft Planetary Computer (Sentinel-2, Landsat, Sentinel-5P), Copernicus
CDS (ERA5).

## 3. Components and decisions

### 3.1 Source control and branches

Existing practice: work on `developpement`, merge into `main`. The design
keeps it:

* Pull requests into `developpement` run CI only.
* Pushes to `developpement` run CI.
* Pushes to `main` and tags `v*` run CI **and** publish an image.

### 3.2 GitHub Actions (planned)

Two workflows, described in [CI_CD.md](CI_CD.md): `ci.yml` (tests, Docker
build without push, manifest validation) and `docker-publish.yml` (GHCR
publish with the built-in `GITHUB_TOKEN`). Free for public repositories.
Private repositories get the GitHub Free minutes quota.

### 3.3 Container image (planned)

One image for every job, `ghcr.io/robindemclu/orbitcu`. It is based on
`python:3.12-slim`, runs as a non-root user and holds the code, the registry
CSVs and the templates. It holds **no data and no secrets**. The package is
installed in editable mode because of `REPO_ROOT` (see
[DOCKER.md](DOCKER.md)).

### 3.4 Scheduler: Kubernetes CronJob rather than Argo Workflows

| Criterion | CronJob + script | Argo Workflows |
|---|---|---|
| Extra components to install | None (built into Kubernetes) | Argo controller, CRDs, UI, RBAC |
| Steps in the daily run | 3–4 sequential CLI calls | Same |
| Useful parallelism | Low. The steps are I/O-bound against the same few APIs, and FIRMS has a per-key quota. | DAG fan-out per source is possible |
| Retry granularity | Whole job (each step is incremental, so a retry is cheap) | Per step |
| Student learning curve | Low | Medium–high |
| Resource cost on a small cluster | One pod | Controller plus pods |

**Decision:** one CronJob `orbitcu-daily` running a sequential script. The
retry cost is low because the commands used daily skip finished work
(`statusmap`, `s5p-batch` and `image-scores` are incremental, see the audit,
§5). Argo stays an option for later, for example when `so2-copper` and the
future fusion/index jobs exist as CLI commands. The report's "Kubernetes +
Argo" plan can be migrated step by step: every script step becomes one Argo
template that uses the same image.

A **separate, manually triggered** Job template handles **historical
backfills** (for example `s5p-batch --start 2022-01 --end 2026-10`), so that
the daily run never downloads the full history.

### 3.5 Storage: durable object storage plus a working PVC

The CLI writes only to local paths. Object storage is therefore reached
through a **copy adapter** (rclone) rather than by changing the code:

* **Durable (source of truth): Azure Blob Storage**, one container `orbitcu`
  with prefixes `raw/`, `processed/` and `final/`. Blob soft delete and
  versioning protect against accidental overwrites. No lifecycle rule ever
  deletes historical data.
* **Working copy: PVC `orbitcu-work`** mounted at `/work`. It holds the same
  `output/` and `cache/` trees the CLI already uses. On a fresh cluster it is
  refilled from Blob by the restore step. Losing it costs time, not data.
* **Ephemeral:** the pod filesystem (`/tmp`) and nothing else.

Why rclone: it supports Azure Blob and every S3-compatible service with the
same commands, it runs on Kind without a CSI driver, and `rclone copy` never
deletes on the destination. A BlobFuse2 / Azure Blob CSI mount is an option
on AKS only, and is not portable to Kind. Full details, the path mapping and
the alternatives are in [STORAGE.md](STORAGE.md).

### 3.6 Secrets

Runtime keys go in Kubernetes Secrets created with `kubectl create secret`
from local files that are never committed. CI uses only the built-in
`GITHUB_TOKEN`; no data-provider key is needed for tests (the tests are
offline). See [SECURITY.md](SECURITY.md).

### 3.7 Where the cluster runs

| Option | Cost | Use |
|---|---|---|
| **Kind** on a team laptop | \$0 | Development and validation (recommended first) |
| **k3s on one small VM** (Azure for Students credit) | Covered by the student credit, as long as it lasts | Unattended daily runs |
| **AKS** (free control plane tier, plus nodes) | Node VM cost | Only if the team needs a managed cluster |

A CronJob on Kind only runs while the laptop is on. Unattended daily
execution therefore needs an always-on host (a VM). Provisioning one is a
**paid / credit-consuming action and needs team approval**.

## 4. Daily data flow (planned)

1. **Restore:** `rclone copy blob:orbitcu/<prefix> /work/...` copies down only
   the files missing from the PVC, which is a no-op when the PVC is intact.
2. **Pipeline** (`infra/scripts/daily.sh`, sequential, `set -euo pipefail`),
   run from `/work`:
   1. `orbitcu statusmap --start <M-11> --end <current month> --firms-dir cache/firms_hist --out output/status_map.html`:
      FIRMS months not yet cached, plus the current month. `statusmap`
      downloads the current month again on every run, but **never refreshes a
      past month that is already cached**. On days 1–5 of a month the script
      therefore first runs
      `orbitcu statusmap --start <previous month> --end <previous month> --refresh --out /tmp/refresh.html`,
      so that the previous month's last hours are not lost.
   2. `orbitcu s5p-batch --start <previous month> --end <current month> --out output`:
      unfinished months only.
   3. `orbitcu image-scores --start-year <current year> --end-year <current year> --out output/image_scores`:
      the current year (see the cost note, audit R9; it may be weekly).
   4. Log scan: fail the job if the output contains `SKIPPED`,
      `RETRY LATER` or `could not be downloaded` (audit R3).
3. **Upload:** `rclone copy /work → blob:` copies new and changed files only
   and never deletes.

`so2-copper` is **not** in the daily run until the `era5_ts` gap is closed
(audit R7). `optical` (clips), `firms` and `s5p` overwrite their per-window
files and are kept for manual or backfill use (audit R5).

## 5. Failure model

| Failure | Effect | Recovery |
|---|---|---|
| Provider/API error during a step | Incremental commands keep finished months; the job fails because of the log scan | Kubernetes retries (`backoffLimit: 2`); otherwise the next daily run fills the gap |
| Pod killed / node restart | The Job controller recreates the pod | Same as above; the PVC and Blob are untouched |
| Upload fails | Data stays on the PVC | Next run's upload copies it |
| PVC lost | Working copy gone | Restore step copies it back from Blob |
| Cluster lost | Everything in the cluster gone | Recreate the cluster, `kubectl apply -k`, recreate the Secrets; the restore step refills the PVC ([OPERATIONS.md](OPERATIONS.md)) |
| Blob object overwritten/deleted by mistake | – | Blob versioning / soft delete (retention window set on the account) |
| Two runs at once | Prevented | `concurrencyPolicy: Forbid` |

## 6. Out of scope

Scientific algorithms, copper modelling, trading strategies and the
dashboard. The infrastructure only needs a CLI command for each of them in
order to schedule it.
