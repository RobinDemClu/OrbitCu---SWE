# OrbitCu — Persistent Storage for Satellite Data

> **Status:**
> * **Existing:** local folders on team laptops (`output/`, `cache/`),
>   written by the CLI. They are git-ignored and **not backed up**.
> * **Planned, not implemented:** an Azure Blob Storage container as the
>   durable copy, a Kubernetes PVC as the working copy, and rclone copy jobs
>   between them. No storage account exists yet, and no backup has been
>   configured or tested. Creating the storage account uses Azure credit and
>   needs team approval.

## 1. What the CLI writes today (verified in the code)

All paths are relative to the working directory, or to `--out` /
`--firms-dir` / `--data`.

| Tier (report) | Path | Written by | Format | Incremental |
|---|---|---|---|---|
| RAW | `cache/firms_hist/<site_id>/<YYYY-MM>.parquet` | `orbitcu statusmap` | Parquet (VIIRS detections, `acquired_at`, `available_at`, `ingested_at`) | Yes, per month |
| RAW | `output/s5p_batch/<YYYY-MM>.parquet` | `orbitcu s5p-batch` | Parquet (SO2 pixels, `acquired_at`, `available_at`, `mode`, `orbit`) | Yes, per month (atomic tmp+rename) |
| RAW | `output/<site_id>/<sensor>/<item_id>.tif` | `orbitcu optical` | GeoTIFF clips about 3 × 3 km | Yes, per clip |
| RAW | `output/<site_id>/<sensor>_manifest.parquet` | `orbitcu optical` | Parquet (PIT dates, cloud, quality, `clip_path`) | **No**, overwritten per run window |
| RAW | `output/<site_id>/firms.parquet` | `orbitcu firms` | Parquet | **No**, overwritten |
| RAW | `output/<site_id>/s5p_so2.parquet`, `s5p_orbits.parquet` | `orbitcu s5p` | Parquet | **No**, overwritten |
| RAW | `output/<site_id>/era5/<YYYY-MM>.nc`, `output/<site_id>/era5.parquet` | `orbitcu era5` | NetCDF, Parquet | `.nc` is never refreshed once present; `.parquet` is overwritten |
| RAW | `output/era5_ts/<site>_<start>_<end>.parquet` | `ingest.era5.retrieve_timeseries` (library only, **no CLI**) | Parquet | Skipped if present |
| PROCESSED | `output/image_scores/<site>_<sensor>_<year>.parquet` (+ `image_scores_all.csv`, `image_scores_monthly.csv`) | `orbitcu image-scores` | Parquet/CSV | Yes, per finished year |
| FINAL | `output/status_map.html` (and `--json`) | `orbitcu statusmap` | HTML / JSON | Rebuilt each run |
| FINAL | `output/so2_copper/*.csv` | `orbitcu so2-copper` | CSV | Rebuilt each run |
| (reference, in Git) | `data/registry/`, `data/reference/`, `data/trading/` | committed by the team | CSV/JSON | – |

Naming already follows **source → site → month/year** for the incremental
paths (`firms_hist/<site>/<month>`, `<site>/<sensor>/<item>`,
`image_scores/<site>_<sensor>_<year>`). `s5p_batch` is organised by month
only, because one file holds all sites. The design keeps these names as
they are. Renaming would break `statusmap --no-download`, `so2-copper --data`
and `production`, which read these exact layouts.

**Metadata.** Observation date (`acquired_at`), availability date
(`available_at`, the no-look-ahead rule in `ingest/pit.py`), its basis
(`pit_basis`), source/sensor, site ID, quality (cloud/shadow fractions,
`qa_value`, image `quality` flag), download timestamp (`ingested_at` for FIRMS
and optical) and the file path (`clip_path`) are **already stored inside the
Parquet files** by the existing code. The storage layer copies files byte for
byte and never rewrites them, so the no-look-ahead information is preserved.

## 2. Durable vs temporary storage

| Kind | Where | Survives pod deletion | Survives cluster loss | Role |
|---|---|---|---|---|
| **Durable** | Azure Blob Storage, container `orbitcu` | Yes | Yes | Source of truth, long-term archive |
| **Working copy** | PVC `orbitcu-work` at `/work` | Yes | **No** (unless the storage class is backed by cloud disks) | Fast filesystem the CLI writes to |
| **Temporary** | `emptyDir` `/tmp` | No | No | Scratch, HOME, the refresh map |
| **Never** | Container image, Git, pod root filesystem | – | – | Imagery must never be stored there |

## 3. Choice of object storage

| Option | Cost for a student team | Pros | Cons |
|---|---|---|---|
| **Azure Blob Storage** (recommended) | Paid from Azure for Students credit (the report states that optional cloud use is covered by Azure for Students) | Students already have it; soft delete and versioning; lifecycle tiers; rclone, azcopy and BlobFuse2 support | The credit is limited and the subscription expires; egress is charged |
| S3-compatible (Cloudflare R2, Backblaze B2, MinIO) | R2/B2 have low prices and/or free tiers; MinIO is self-hosted | Same rclone commands | Usually needs a credit card; another account to manage |
| PVC only | \$0 on Kind | Simplest | Lost with the laptop or cluster: **not acceptable** as the only copy |

**Prices change. Check the Azure pricing calculator for the region you pick
before creating anything.** The main cost drivers are stored GB-month
(Hot/Cool tier), write/read operations (many small Parquet files mean many
operations) and egress. The data volume has **not been measured** yet. Run
`du -sh output cache` on the laptop that holds the largest download before
sizing the account. Recommended settings: Standard general-purpose v2, LRS,
Hot tier, one container `orbitcu` with private access only.

## 4. Planned layout in Blob

The blob container mirrors `/work` one to one, so restore is a plain copy and
the CLI paths stay valid:

```
orbitcu/                       (container)
  cache/firms_hist/<site>/<YYYY-MM>.parquet         RAW
  output/s5p_batch/<YYYY-MM>.parquet                RAW
  output/<site>/<sensor>/<item_id>.tif              RAW
  output/<site>/...manifest/era5 files              RAW
  output/image_scores/...                           PROCESSED
  output/status_map.html, status_map.json           FINAL
  output/so2_copper/...                             FINAL
  logs/daily-<UTC timestamp>.log                    run logs
```

The RAW, PROCESSED and FINAL tiers of the report are kept as a **logical
classification** (the table in §1). They are not physical prefixes, because
the existing code mixes RAW and FINAL under `output/`. Physical prefixes
would need code or CLI-option changes: a possible later step, with team
approval.

## 5. How data moves (adapter)

The code can only write to local paths, so object storage is reached with
**rclone** (`rclone/rclone:1.68.2`):

| Step | Command | Deletes anything? |
|---|---|---|
| Restore before each daily run (init container) | `rclone copy --ignore-existing orbitcu-blob:orbitcu /work` | No. It only adds files that are missing on the PVC. |
| Upload after the run (`orbitcu-upload` CronJob) | `rclone copy /work orbitcu-blob:orbitcu --exclude "*.tmp"` | No. It adds new files and **replaces changed ones**, which are kept as previous versions if versioning is on. |

**`rclone sync` must never be used:** it deletes files on the destination.
For the same reason, no "delete" or lifecycle-expiry rule may be configured
on RAW data.

Example `rclone.conf` (stored only in a Kubernetes Secret, never in Git; see
[SECURITY.md](SECURITY.md)):

```ini
[orbitcu-blob]
type = azureblob
account = <storage-account-name>
sas_url = <SAS URL limited to the orbitcu container, permissions r,a,c,w,l (no d)>
```

A SAS without the **delete** permission is a second safety net: even a
mistaken `rclone sync` could not delete blobs.

S3-compatible alternative:

```ini
[orbitcu-blob]
type = s3
provider = Cloudflare        # or Backblaze, Minio, Other
access_key_id = <id>
secret_access_key = <secret>
endpoint = <endpoint-url>
```

Here the `RCLONE_REMOTE` value in the ConfigMap becomes
`orbitcu-blob:<bucket>`.

**Limitations of this approach**

* The PVC holds a **full copy** of everything restored. It must be as large
  as the archive the daily run needs. Old clips that are never re-read could
  later be excluded from the restore (`--exclude "output/*/sentinel2/**"`),
  because the CLI skips existing clips only if they are on disk.
* The upload runs at a fixed time after the pipeline. Data produced by a run
  that is still going at 12:00 UTC (which should not happen, because of the
  4 h deadline) would be uploaded the next day.
* A file that is corrupt on the PVC (for example after a kill during a
  non-atomic write) would overwrite the good blob version. Versioning lets
  you recover the previous version (§7).

Alternatives considered: the **Azure Blob CSI driver / BlobFuse2** (mount the
container directly) works on AKS, but needs privileged node components that
Kind does not have, and Parquet over FUSE is slow. **Writing directly from
Python** (fsspec/adlfs) would mean changing every `Path` write in the code,
which is out of scope.

## 6. Avoiding duplicate downloads and safe retries

* Duplicates are avoided **by the existing CLI**. Files already present are
  skipped (audit §5), and the restore step puts them back on the PVC before
  each run.
* Retries are safe for the commands used daily: `statusmap` never saves an
  incomplete month, `s5p-batch` never saves a month with failed orbits and
  writes atomically, and `image-scores` never saves a year with more than
  20 % failed scenes.
* Not idempotent (keep them out of scheduled runs): `firms`, `s5p` and
  `optical` manifests, and `era5` (audit R5/R6).

## 7. Retention, protection and recovery

**Retention policy (proposed):** keep all RAW data indefinitely. Never delete
historical satellite data automatically.

| Protection | Setting | Status |
|---|---|---|
| Blob soft delete | 30 days | Planned (set when the account is created) |
| Container soft delete | 30 days | Planned |
| Blob versioning | On | Planned. Old versions cost storage, so a lifecycle rule may delete **previous versions only** after 90 days. Current versions are never deleted. |
| Lifecycle tiering | Optional: move current blobs not modified for 180 days to Cool | Optional (Cool has read and early-deletion fees) |
| SAS without delete permission | Upload/restore identity cannot delete | Planned |
| Second copy (backup) | None yet | **Not configured.** Options: Azure object replication to a second account, or a periodic `rclone copy` to a second provider |

**Recovery procedures** (step by step in [OPERATIONS.md](OPERATIONS.md), §6):

* Pod deleted or failed: nothing to do. The Job retries, and the PVC is
  intact.
* PVC or node lost: recreate the PVC (`kubectl apply -k`). The next daily
  run's restore init container copies everything back from Blob.
* Cluster lost: recreate the cluster and the Secrets, then `kubectl apply -k`.
  Same as above.
* Blob overwritten or deleted by mistake: restore the previous version or
  undelete within the soft-delete window (Azure portal or `az storage blob
  undelete`).
* Storage account deleted: **not recoverable** without a second copy. Protect
  it with an Azure resource lock (`CanNotDelete`).

## 8. Validation checklist (to run once implemented)

This is the Phase 10 test plan. None of these steps has been run yet. Each
step is described in [OPERATIONS.md](OPERATIONS.md), §5.

1. Download a small dataset (one site, one month).
2. Check that it exists in Blob (`rclone ls`).
3. Delete the pod / Job and check that the data is still on the PVC and in
   Blob.
4. Run the same download again and check that nothing new is downloaded
   ("everything already cached").
5. Simulate a failure (wrong key) and check that the Job is marked Failed
   and retried.
6. Check the logs (`kubectl logs`, `logs/` in Blob).
7. Check that the blob count did not decrease (`rclone size` before and
   after).
