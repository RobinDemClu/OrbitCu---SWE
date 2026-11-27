# OrbitCu — Infrastructure Implementation Status

> Last updated: **27 November 2026** (branch `feature/phase2-infrastructure-docs`).
> Legend: ✅ exists and verified · 🧪 designed and tested locally, not
> committed · 📝 designed (documented only) · ⛔ blocked / needs a decision
> · ❌ not started.

## 1. Summary

The **application** works and is run by hand. The **infrastructure** (Docker,
CI, Kubernetes, cloud storage, daily automation) is **designed and documented
but not implemented**: there is no `Dockerfile`, no `.github/workflows/`, no
`infra/` and no cloud resource. Some designs were tested in a sandbox and are
marked 🧪. Nothing has been deployed to any cluster or cloud.

## 2. Existing functionality (verified)

| Item | Status | Evidence |
|---|---|---|
| `orbitcu` CLI: `firms`, `optical`, `s5p`, `s5p-batch`, `era5`, `statusmap`, `image-scores`, `so2-copper` | ✅ | `--help` of every command; [INFRASTRUCTURE_AUDIT.md](INFRASTRUCTURE_AUDIT.md) §4 |
| Unit tests | ✅ 53 passed | `pytest` on Python 3.13.18 with all extras |
| Incremental, resumable downloads (`statusmap` FIRMS cache, `s5p-batch`, `image-scores`) | ✅ (code reading) | Audit §5 |
| No-look-ahead metadata (`acquired_at`, `available_at`, `pit_basis`) | ✅ (code reading) | `src/orbitcu/ingest/pit.py` |
| Smelter registry (29 sites) | ✅ | `data/registry/smelter_sites.csv` |
| Secrets kept out of Git | ✅ | History scan ([SECURITY.md](SECURITY.md) §7) |
| Downloaded data kept out of Git | ✅ | `.gitignore` has `/output/`, `/cache/`; no imagery tracked |

## 3. Infrastructure

| Component | Target file(s) | Status | Notes |
|---|---|---|---|
| Documentation set | `README.md`, `docs/*.md` | ✅ | This phase |
| Dockerfile | `Dockerfile` | 🧪 | Built and smoke-tested in a sandbox. The `apt-get` line (libexpat1, tini) could not be tested there ([DOCKER.md](DOCKER.md) §6) |
| `.dockerignore` | `.dockerignore` | 🧪 | Used in the sandbox build |
| `.env.example`, `.gitignore` additions | `.env.example`, `.gitignore` | 📝 | [SECURITY.md](SECURITY.md) §4 |
| CI workflow | `.github/workflows/ci.yml` | 📝 | YAML parsed; never run on GitHub |
| Image publishing (GHCR) | `.github/workflows/docker-publish.yml` | 📝 | YAML parsed; never run. The package must be made public, or a pull secret used |
| Kubernetes manifests | `infra/kubernetes/*.yaml` | 🧪 | Structural checks passed; `kubectl kustomize`, kubeconform and `apply --dry-run=server` **not run** |
| Daily pipeline script | `infra/scripts/daily.sh` | 🧪 | Dry run, missing-key and SIGTERM tests passed in the local image; no real API run |
| Durable object storage (Azure Blob) | Azure resources | ⛔ | Needs team approval (uses Azure for Students credit) and an account owner |
| Upload/restore adapter (rclone) | In the manifests | 📝 | Not tested (no storage account) |
| Backup / second copy | – | ❌ | Not configured. Only soft delete and versioning are planned |
| Always-on cluster for unattended runs | VM / k3s or AKS | ⛔ | Needs approval; Kind runs only while a laptop is on |
| Monitoring / alerting | – | ❌ | Manual `kubectl get jobs` check for now |
| Validation on Kind (Phase 10 plan) | – | ❌ | [OPERATIONS.md](OPERATIONS.md) §5 |

## 4. Gaps in the application that limit automation

These need **small code changes**, and the code owners have to agree first.
The infrastructure work did not change any application code.

| # | Gap | Effect | Smallest fix |
|---|---|---|---|
| G1 | No CLI command produces `output/era5_ts/` | `so2-copper` cannot run in the daily pipeline | Add `orbitcu era5-ts` that wraps `era5.retrieve_timeseries` |
| G2 | `s5p-batch`, `statusmap` and `image-scores` exit 0 on partial failure | Kubernetes reports success | Return a non-zero exit code (the script's log scan works around it for now) |
| G3 | Direct `to_parquet` writes in `statusmap` and `image_scores` | A kill mid-write can leave a corrupt file that is then skipped | Write to `*.tmp`, then rename (as `s5p_batch` already does) |
| G4 | `optical`/`firms`/`s5p` overwrite per-window files; `era5` never refreshes a month | Not usable for daily accumulation | Per-month files, or append mode |
| G5 | Registry path tied to `__file__` (`REPO_ROOT`) | Forces an editable install | Optional `ORBITCU_REGISTRY_DIR` override |
| G6 | Fusion, indices, copper estimate and trading have no CLI | They cannot be scheduled | Add commands as these modules mature |
| G7 | No pinned dependencies | Builds are not reproducible | Constraints file from CI |
| G8 | README says 24 tests, the report says 42, the real count is 53 | Confusing | README fixed in this phase; the LaTeX report still says 42 |

## 5. Next steps (in order)

1. **Review and merge this documentation PR** into `developpement`.
2. Add `Dockerfile`, `.dockerignore`, `.env.example` and the `.gitignore`
   additions. Build them on a team laptop to confirm the `apt-get` line.
3. Add `.github/workflows/ci.yml` (the `tests` job first). Check Python 3.10.
   Turn on branch protection on `main`.
4. Add `infra/kubernetes/` and `infra/scripts/daily.sh`, plus the
   `docker-build` and `k8s-manifests` CI jobs.
5. Add `docker-publish.yml`, publish the first image from `main`, and make
   the GHCR package public.
6. Team decision: create the Azure Storage account (settings in
   [ENVIRONMENT_SETUP.md](ENVIRONMENT_SETUP.md) §4). Measure the current data
   volume (`du -sh output cache`) and upload the existing laptop data once
   with `rclone copy`.
7. Run the Phase 10 validation on Kind ([OPERATIONS.md](OPERATIONS.md) §5)
   and record the results here.
8. Team decision: an always-on host for unattended daily runs.
9. With the code owners: G1–G3, then add `so2-copper` to the daily script.
10. Later: Argo Workflows once several dependent jobs exist; a backup copy;
    failure notifications.
