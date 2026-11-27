# OrbitCu — Persistent Storage for Satellite Data

> **Status: design, not implemented.** Today all downloaded data live on the
> team members' local disks, under `output/` and `cache/`, which Git ignores.
> There is **no** shared storage, **no** object storage, **no** PVC and **no
> backup**. This document specifies what Phases 6 and 10 will build. It does
> not claim that any backup exists.

## 1. Requirements

- Satellite data and results survive pod restarts, pod deletion, job failures
  and the loss of the cluster.
- Downloads stay incremental and reruns stay safe. Both properties come from
  the existing code (see §3). The storage layer must not break them.
- No satellite data in Git or in Docker images.
- No automatic deletion of historical data.
- `acquired_at` / `available_at` (no look-ahead) are kept exactly as written.

## 2. Storage classes: durable vs temporary

| Class | Where | Survives pod deletion | Survives cluster loss | Use |
|---|---|---|---|---|
| **Durable** | Object storage (Azure Blob container `orbitcu-data`) | yes | **yes** | Source of truth: RAW, PROCESSED, FINAL |
| **Working copy** | Kubernetes PVC `orbitcu-data` mounted at `/data` | yes | no (node/cluster disk) | The filesystem the CLI reads and writes; rebuilt from durable storage |
| **Temporary** | `emptyDir` at `/tmp` in the pod | no | no | Scratch files only |
| **Never** | Container root filesystem, Git, image layers | — | — | Nothing is stored there |

## 3. How the existing code achieves incremental, idempotent downloads

The infrastructure relies on these behaviours, which were checked in the code
(see [INFRASTRUCTURE_AUDIT.md §5](INFRASTRUCTURE_AUDIT.md#5-idempotency-incremental-behaviour-failure-handling)):

| Command | Unit of work | "Done" test | Unfinished unit |
|---|---|---|---|
| `statusmap` | site × month | file exists and month < current month | current month downloaded again; a month with a failed request is not written |
| `s5p-batch` | month (all sites) | file exists and `month end + 5 days` has passed | redone; written to `.tmp` and renamed (atomic) |
| `image-scores` | site × sensor × year | file exists and `Jan 31 of next year` has passed | redone; a year with > 20 % failed scenes is not written |
| `optical` (clips) | scene | `<item_id>.tif` exists | downloaded |

Because "done" is a test on **file existence**, the code must see the full
history as a local filesystem. That is the main reason for the PVC working copy.

## 4. Layout

The code has no RAW/PROCESSED/FINAL split (audit A-4). The split is made
**only through existing CLI arguments**, with no code change:

```
/data                                         (PVC mount; same tree in the bucket)
├── raw/
│   ├── firms_hist/<site_id>/<YYYY-MM>.parquet        statusmap --firms-dir /data/raw/firms_hist
│   ├── s5p_batch/<YYYY-MM>.parquet                   s5p-batch --out /data/raw
│   ├── era5_ts/<site_id>_<start>_<end>.parquet       (no CLI; copied in by hand, see §7)
│   └── backfill/<site_id>/...                        manual firms/optical/s5p/era5 runs (--out /data/raw/backfill)
├── processed/
│   └── image_scores/<site>_<sensor>_<year>.parquet   image-scores --out /data/processed/image_scores
│       image_scores_all.csv, image_scores_monthly.csv
├── final/
│   ├── status_map.html                               statusmap --out /data/final/status_map.html
│   └── so2_copper/*.csv                              so2-copper --data /data/raw --out /data/final/so2_copper
└── logs/<YYYY-MM-DD>/<job>.log                       written by the planned pipeline script
```

Organisation by source, site, year and month follows the code's existing
naming:
- FIRMS: by site, then month.
- S5P: by month, with all sites in one file.
- Image scores: by site, sensor and year.

Introducing more partition levels (e.g. `year=/month=`) would mean changing
the ingestion code and is **not** planned.

Placing `era5_ts/` next to `s5p_batch/` under `raw/` is required by
`so2-copper`, which reads `<--data>/s5p_batch/` and `<--data>/era5_ts/`.

## 5. Synchronisation with object storage

**Tool:** [`rclone`](https://rclone.org) (one static binary, supports Azure
Blob and S3). It runs as the last step of each Job, using a small image or
the binary added to the OrbitCu image.

| Operation | Command (planned) | Deletes anything? |
|---|---|---|
| Push after a run | `rclone copy /data orbitcu:orbitcu-data --checksum --exclude "**/*.tmp" --exclude "**/*.part"` | **No**: `copy` only adds and updates |
| Restore to a new PVC | `rclone copy orbitcu:orbitcu-data /data --checksum` | No |
| Check | `rclone check /data orbitcu:orbitcu-data --one-way` | No |

`rclone sync` (which deletes files at the destination) is **forbidden** in
the manifests and the scripts. `rclone` reads its remote from environment
variables in the Secret, for example `RCLONE_CONFIG_ORBITCU_TYPE=azureblob`,
`RCLONE_CONFIG_ORBITCU_ACCOUNT`, and `RCLONE_CONFIG_ORBITCU_SAS_URL` or a key.
See [SECURITY.md](SECURITY.md).

Files that are re-written on purpose (the current month in `firms_hist`,
unfinished `s5p_batch` months, CSV results) are protected by **blob
versioning**: the previous version stays recoverable.

### Why not mount the bucket directly?

| Option | For | Against |
|---|---|---|
| **PVC + rclone copy** (chosen) | Works on Kind, k3s and AKS; code unchanged; fast local I/O; no-delete guarantee | Two copies; a run that crashes before the push loses only that run's new files, which the next run downloads again |
| Azure Blob CSI driver (blobfuse2) | One copy | AKS-specific; not on Kind; cache semantics, renames and `exists()` checks are slower and less predictable |
| Change the code to write to object storage (fsspec) | Clean long-term | Changes every ingest module, out of infrastructure scope |

## 6. Backend choice: Azure Blob Storage

- **Why:** the report says optional cloud use is covered by Azure for
  Students. Azure Blob has versioning, soft delete, lifecycle tiering, and
  SAS tokens limited to one container.
- **Account settings (to be created manually, with approval):**
  - StorageV2, **LRS**, access tier **Hot** at first.
  - Container `orbitcu-data`, private.
  - **Blob versioning ON**, **soft delete ON** for blobs and containers (for
    example 30 days).
  - Public access disabled.
- **Fallback:** any S3-compatible store (Cloudflare R2, Backblaze B2, MinIO
  on the k3s VM for tests). Only the rclone remote changes.

## 7. Known gaps

| Gap | Effect | Proposed fix | Owner |
|---|---|---|---|
| No CLI for ERA5 point series (`era5.retrieve_timeseries`) | `so2-copper` cannot get new winds automatically | Copy the existing `era5_ts/` files from a laptop into the bucket once; later add an `orbitcu era5-ts` command | SO2 owner (Ethan), with infrastructure |
| Window-overwriting commands (`firms`, `optical` manifest, `s5p`, `era5` table) | Their files hold only the last window | Run them only as backfills into `raw/backfill/`, with full windows | — |
| Non-atomic writes in most commands | Half-written file after a kill | Restore the previous blob version; later, write-then-rename in the code | module owners |
| Outputs produced outside the CLI (`activity_index/…`, trading inputs) | Not reproducible by the pipeline | Copy them under `final/` by hand until entry points exist | Robin / Ethan |

## 8. Cost estimate

Volumes are **not measured yet**. The order of magnitude below comes from the
code:
- A clip is ~90 kB (docstring of `ingest/optical.py`).
- `image-scores` stores only score rows, not clips.
- `s5p_batch` and `firms_hist` are compact Parquet tables.

A total in the **low tens of GB** is the planning assumption. It must be
replaced by `rclone size` once the first upload has been done.

| Item | Order of magnitude (check the Azure pricing calculator for your region) |
|---|---|
| Blob Hot LRS storage | about \$0.02 per GB-month → under \$1/month for 20 GB |
| Write / read operations | a few cents per 10 000 operations; daily runs touch few files |
| Egress | restores to a cluster outside Azure are billed per GB; rare |
| Versioning | every overwritten version is billed as stored data; small here |

The compute side is cheapest with Kind on a team machine (\$0, but only runs
while the machine is on) or k3s on one small VM paid from student credit.
**No paid resource is created without the team's explicit approval.**

## 9. Retention and lifecycle

- **Never delete automatically.** No lifecycle rule deletes blobs.
- Optional lifecycle rule: move `raw/` blobs not modified for 90 days to the
  **Cool** tier, which is cheaper to store and dearer to read. This is useful
  once the history is complete.
- Noncurrent versions: keep 90 days, then delete. This is the **only**
  deletion rule, and it applies to superseded versions, never to current
  files.
- Kubernetes keeps the last 3 successful and 5 failed Jobs (CronJob history limits).

## 10. Recovery

| Lost | Recovery |
|---|---|
| Pod / Job | Nothing to do: the next run (or `kubectl create job --from=cronjob/orbitcu-daily …`) resumes from the PVC |
| PVC / node / whole cluster | Re-create the cluster and apply the manifests, run the restore Job (`rclone copy` bucket → PVC), then run the daily Job |
| A corrupted or overwritten file | Restore the previous blob version (Azure portal or `az storage blob` with `--version-id`), or delete the local file so that the command downloads it again |
| Bucket deleted by mistake | Container soft delete (if enabled) within the retention window; otherwise re-download from the providers. All sources are public archives; only the `era5_ts` files and the outputs produced outside the CLI would need manual work |

## 11. Validation procedure (Phase 10, to be run and reported)

Run on Kind first, then on the target cluster. Record results in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

1. Run a small download: `s5p-batch --sites PL-GLOGOW --start <YYYY-MM> --end <YYYY-MM> --out /data/raw`.
   Pick a month that ended more than 5 days ago, otherwise step 5 redoes it on purpose.
2. Check that the file exists on the PVC and, after the push, in the bucket (`rclone ls orbitcu:orbitcu-data/raw/s5p_batch`).
3. Delete the pod (`kubectl delete pod …`), and also the Job.
4. Start a new pod and check that the file is still on the PVC.
5. Run the same command again. The month is skipped (no catalogue search in
   the logs) and the file's modification time does not change.
6. Simulate a failure: run with no network policy for egress, or `kubectl delete pod` mid-run.
7. Check that the Job retries (`backoffLimit`), that the old month files are
   unchanged, and that the next run completes the work.
8. Check the logs: `kubectl logs job/<name>` and `/data/logs/<date>/`.
9. Delete the PVC, run the restore Job, and check with `rclone check` that
   the files are back.
10. Check that no file in the bucket was deleted: compare the
    `rclone size` / `rclone lsf -R` counts before and after.
