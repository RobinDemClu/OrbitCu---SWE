# OrbitCu — Infrastructure Implementation Status

Last updated: **27 November 2026**, branch `feature/kubernetes-infrastructure`.

Legend:
- **EXISTS**: in the repository and verified.
- **DOCUMENTED**: the design is written down, and no file or resource exists yet.
- **NOT STARTED**: no design and no file yet.
- **BLOCKED**: needs a decision, a code change by a module owner, or a paid resource.

Nothing in this table claims that something is deployed, scheduled or backed
up unless it is marked EXISTS.

## 1. Application (not owned by infrastructure; listed for context)

| Item | Status | Evidence |
|---|---|---|
| `orbitcu` CLI: `firms`, `optical`, `s5p`, `era5`, `s5p-batch`, `statusmap`, `image-scores`, `so2-copper` | EXISTS | `src/orbitcu/cli.py`; `orbitcu --help` run on 27 Nov 2026 |
| Point-in-time timestamps (`acquired_at`, `available_at`) | EXISTS | `src/orbitcu/ingest/pit.py` |
| Resumable, month-partitioned downloads (`statusmap`, `s5p-batch`, `image-scores`) | EXISTS | see [INFRASTRUCTURE_AUDIT.md §5](INFRASTRUCTURE_AUDIT.md#5-idempotency-incremental-behaviour-failure-handling) |
| Unit tests | EXISTS: **53 passed** (Python 3.13, 27 Nov 2026) | `pytest -q` |
| CLI for the ERA5 point series used by `so2-copper` | **BLOCKED**: no command | `era5.retrieve_timeseries` has no caller |
| CLI for activity index, production estimates, trading | **BLOCKED**: no command | `activity_index`, `production`, `trading` are library-only; `scripts/` read `$ORBITCU_DATA` |

## 2. Infrastructure

| Phase | Item | Status | Where |
|---|---|---|---|
| 1 | Repository audit | EXISTS | [INFRASTRUCTURE_AUDIT.md](INFRASTRUCTURE_AUDIT.md) |
| 2 | Architecture and design docs | EXISTS (this phase) | [ARCHITECTURE.md](ARCHITECTURE.md) and the other `docs/*.md` |
| 3 | `Dockerfile`, `.dockerignore` | DOCUMENTED | [DOCKER.md](DOCKER.md) |
| 3 | `constraints.txt` (pinned dependencies) | DOCUMENTED | [DOCKER.md §1](DOCKER.md#1-requirements-from-the-audit) |
| 3 | Image built and CLI tested in a container | NOT STARTED | Docker daemon not available in the audit environment |
| 4 | `.github/workflows/ci.yml` | DOCUMENTED | [CI_CD.md §2](CI_CD.md#2-ciyml--tests-and-validation-planned) |
| 4 | Tests on Python 3.10 (declared minimum) | NOT STARTED | will run in CI |
| 5 | `.github/workflows/docker-publish.yml`, GHCR image | DOCUMENTED | [CI_CD.md §3](CI_CD.md#3-docker-publishyml--publish-to-ghcr-planned) |
| 6 | Object storage (Azure Blob container `orbitcu-data`) | **BLOCKED**: needs team approval to create a cloud resource | [STORAGE.md](STORAGE.md) |
| 6 | RAW / PROCESSED / FINAL layout via CLI arguments | DOCUMENTED | [STORAGE.md §4](STORAGE.md#4-layout) |
| 6 | rclone push / restore | DOCUMENTED | [STORAGE.md §5](STORAGE.md#5-synchronisation-with-object-storage) |
| 7 | `infra/kubernetes/` base + overlays (Kustomize) | DOCUMENTED | [KUBERNETES.md](KUBERNETES.md) |
| 7 | Manifests validated (`kubectl kustomize`, `kubeconform`, dry-run) | NOT STARTED | — |
| 8 | `infra/scripts/daily_pipeline.sh` | DOCUMENTED (text checked with `bash -n`) | [OPERATIONS.md §2](OPERATIONS.md#2-planned-daily-pipeline-infrascriptsdaily_pipelinesh) |
| 8 | Daily CronJob running somewhere | NOT STARTED | needs a cluster (Kind / k3s) |
| 8 | Backfill jobs | DOCUMENTED | [OPERATIONS.md §3](OPERATIONS.md#3-historical-backfills-separate-from-the-daily-schedule) |
| 9 | Secret handling, `.env.example`, `.gitignore` additions | DOCUMENTED (`.env` already ignored) | [SECURITY.md](SECURITY.md) |
| 10 | Storage validation (10-step procedure) | NOT STARTED | [STORAGE.md §11](STORAGE.md#11-validation-procedure-phase-10-to-be-run-and-reported) |
| 10 | Backup | **NOT CONFIGURED**: no backup exists | — |
| 11 | README infrastructure section | EXISTS (this phase) | [README.md](../README.md) |
| 12 | End-to-end validation in a cluster | NOT STARTED | — |

## 3. Open decisions (team)

1. **Where the daily runs execute:** an always-on team machine (free), or one
   small VM with k3s paid from Azure for Students credit.
2. **Create the Azure Storage account?** It is cheap, but it is a cloud
   resource and needs explicit approval.
3. **Public or private GHCR package.** Public is simpler, and the image holds no secret or data.
4. **ERA5 point series:** who adds a CLI command, or whether the existing
   `era5_ts/` files are uploaded once by hand.
5. **Optional small code changes**, to be proposed to the module owners
   rather than made by infrastructure:
   - non-zero exit code when `s5p-batch` or `statusmap` skip months;
   - atomic writes in `statusmap` and `image-scores`;
   - catching `requests.RequestException` in `firms._get`.

## 4. Discrepancies found in existing documentation

| Where | Says | Actual (27 Nov 2026) | Action |
|---|---|---|---|
| `README.md` | "24 unit tests" | 53 test cases (51 test functions) | README updated in this phase |
| `docs/latex/orbitcu_report.tex`, "Using the code" | "42 tests" | 53 | left for the report owner (the PDF must be recompiled) |
| Report, architecture figure | orchestration "Kubernetes + Argo (planned)" | CronJobs chosen first, Argo deferred | explained in [ARCHITECTURE.md §4 D1](ARCHITECTURE.md#d1-cronjobs-not-argo-workflows-for-now) |
