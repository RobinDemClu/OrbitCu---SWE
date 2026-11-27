# OrbitCu — Operations, Daily Automation and Troubleshooting

> **Status:** sections 1 and 5.1 describe how the project is run **today**
> (by hand). Sections 2–4 describe the **planned** automated operation. The
> pipeline script, the CronJobs and the storage sync do **not exist yet**.

## 1. Today: manual operation

The team runs the commands on laptops. The commands that keep a growing
history and can be rerun safely are:

```bash
orbitcu statusmap --end 2026-11
```

```bash
orbitcu s5p-batch --start 2026-10 --end 2026-11
```

```bash
orbitcu image-scores --start-year 2026 --end-year 2026
```

```bash
orbitcu so2-copper --data output --out output/so2_copper
```

- The `statusmap` command defaults to the 12 months up to the last complete
  month. `--end <current month>` also refreshes the current month.
- `so2-copper` needs `output/era5_ts/` to exist (produced by
  `orbitcu.ingest.era5.retrieve_timeseries`, which has no CLI command).

## 2. Planned daily pipeline (`infra/scripts/daily_pipeline.sh`)

Every step is an **existing** command with **existing** arguments. The
script adds no new CLI command.

```bash
#!/usr/bin/env bash
# Planned - not yet in the repository.
set -uo pipefail
D="${DATA_ROOT:-/data}"
TODAY=$(date -u +%F)
NOW=$(date -u +%Y-%m)
PREV=$(date -u -d "$(date -u +%Y-%m-01) -1 month" +%Y-%m)
FIRST=$(date -u -d "$(date -u +%Y-%m-01) -11 month" +%Y-%m)
LOG="$D/logs/$TODAY"; mkdir -p "$LOG" "$D/raw" "$D/processed" "$D/final"
rc=0

step() {  # step <name> <command...>: run, keep log, remember failure, continue
  local name=$1; shift
  echo "=== $name: $*"
  "$@" 2>&1 | tee "$LOG/$name.log"
  local s=${PIPESTATUS[0]}
  [ "$s" -ne 0 ] && { echo "!!! $name failed with code $s"; rc=1; }
  return 0
}

# 1. VIIRS heat detections (FIRMS) + status map: missing months and the current month
step statusmap orbitcu statusmap --start "$FIRST" --end "$NOW" \
     --firms-dir "$D/raw/firms_hist" --out "$D/final/status_map.html" \
     --json "$D/final/status_map.json" --workers "${FIRMS_WORKERS:-4}"

# 2. Sentinel-5P SO2: previous + current month (unfinished months are redone by the command)
step s5p-batch orbitcu s5p-batch --start "$PREV" --end "$NOW" --out "$D/raw" \
     --workers "${S5P_WORKERS:-2}"

# 3. SO2 -> copper (needs $D/raw/era5_ts; PLACEHOLDER: no CLI refreshes era5_ts yet)
if compgen -G "$D/raw/era5_ts/*.parquet" > /dev/null; then
  step so2-copper orbitcu so2-copper --data "$D/raw" --out "$D/final/so2_copper"
else
  echo "--- so2-copper skipped: $D/raw/era5_ts is empty (see docs/STORAGE.md section 7)"
fi

# 4. Partial failures that the CLI reports with exit code 0
if grep -hE "SKIPPED|RETRY LATER|could not be downloaded" "$LOG"/*.log > "$LOG/gaps.txt"; then
  echo "!!! incomplete months, will be retried by the next run:"; cat "$LOG/gaps.txt"
  [ "$rc" -eq 0 ] && rc=3
fi

# 5. Push to durable storage (copy, never sync/delete), even after a partial failure
rclone copy "$D" orbitcu:orbitcu-data --checksum \
  --exclude "**/*.tmp" --exclude "**/*.part" || { echo "!!! rclone push failed"; rc=2; }

exit "$rc"
```

| Exit code | Meaning | Kubernetes behaviour |
|---|---|---|
| 0 | all steps done, no gaps | Job `Complete` |
| 1 | a command failed | Job retried (`backoffLimit: 2`), then `Failed` |
| 2 | push to object storage failed | retried; the data are still on the PVC |
| 3 | commands succeeded but some months were skipped | retried; finished months are skipped, so the retry only does the gaps |

How the requirements are met:

| Requirement | How |
|---|---|
| Only incremental downloads | `statusmap` and `s5p-batch` skip finished months on the PVC |
| Process after download | `so2-copper` runs after `s5p-batch` in the same pod |
| Keep previous successful data | Months are written only when complete. `s5p-batch` writes atomically. rclone never deletes |
| Reruns do not corrupt data | Same files are recomputed from the same sources; blob versioning keeps the previous version |
| No concurrent runs | `concurrencyPolicy: Forbid` |
| Retry transient failures | The code retries each request, and Kubernetes retries the Job |
| Visible failures | Exit code → Job status; `gaps.txt`; logs in pod and in `/data/logs` |
| No full-history download daily | Windows are limited to 12 months (FIRMS, mostly cached) and 2 months (S5P) |

## 3. Historical backfills (separate from the daily schedule)

Backfills are one-off Kubernetes Jobs created from the same image. Examples,
using existing commands:

```bash
kubectl -n orbitcu create job orbitcu-backfill-s5p --from=cronjob/orbitcu-daily --dry-run=client -o yaml > /tmp/backfill.yaml
```

In `/tmp/backfill.yaml`, replace `command:` with:

```
["orbitcu", "s5p-batch", "--start", "2022-01", "--end", "2026-10", "--out", "/data/raw", "--workers", "2"]
```

Then apply it:

```bash
kubectl apply -f /tmp/backfill.yaml
```

Other backfill commands that exist today:

```
orbitcu statusmap --start 2018-01 --end 2026-10 --firms-dir /data/raw/firms_hist --out /data/final/status_map.html
orbitcu image-scores --start-year 2018 --end-year 2026 --out /data/processed/image_scores   # ~3 h on a laptop (report)
```

Rules for backfills:
- Run them while `orbitcu-daily` is not active. Suspend the CronJob with
  `kubectl -n orbitcu patch cronjob orbitcu-daily -p '{"spec":{"suspend":true}}'`
  and resume it afterwards. This avoids FIRMS quota contention and two
  writers on the same files.
- Use `--out /data/raw/backfill` for the per-site commands (`firms`,
  `optical`, `s5p`, `era5`), because they overwrite their output with the
  requested window.
- Push with `rclone copy` at the end, as the daily script does.

## 4. Runbooks

### Run the daily job now

```bash
kubectl -n orbitcu create job --from=cronjob/orbitcu-daily orbitcu-daily-manual-$(date +%s)
```

### Pause or resume automation

```bash
kubectl -n orbitcu patch cronjob orbitcu-daily -p '{"spec":{"suspend":true}}'
```

```bash
kubectl -n orbitcu patch cronjob orbitcu-daily -p '{"spec":{"suspend":false}}'
```

### Deploy a new image
Follow [CI_CD.md §4](CI_CD.md#4-deployment-cd--manual-on-purpose).

### Rebuild after cluster loss
1. Create the cluster (Kind or k3s).
2. Create the namespace and the Secret ([KUBERNETES.md §4](KUBERNETES.md#4-planned-commands)).
3. Apply the overlay. This creates the empty PVC and **suspended** CronJobs.
4. Apply `job-restore.yaml`, then wait for it with:

```bash
kubectl -n orbitcu wait --for=condition=complete job/orbitcu-restore --timeout=2h
```

5. Check the restore: `rclone check /data orbitcu:orbitcu-data --one-way`,
   run from a debug pod.
6. Un-suspend the CronJobs.

## 5. Troubleshooting

### 5.1 Already relevant today

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: FIRMS_MAP_KEY is not set` | key missing | Set `FIRMS_MAP_KEY` (or use `statusmap --no-download`) |
| `KeyError: 'CDSAPI_KEY'` | ERA5 key missing | Set `CDSAPI_KEY` |
| `FIRMS quota reached: pausing 60 s` | 5000 transactions / 10 min | Normal. Lower `--workers`; never run two FIRMS jobs at once |
| `FIRMS request failed (quota still exhausted or server unreachable)` | quota pause > 15 min, or FIRMS down | Rerun later. Finished months are kept |
| `SKIPPED 2026-07: …` (s5p-batch) | catalogue or network error | Rerun. The month is redone |
| `RETRY LATER <site> <sensor> <year>` (image-scores) | > 20 % of scenes failed | Rerun later |
| `KeyError: unknown site ids` | wrong `--sites` value | Use the `id` column of `data/registry/smelter_sites.csv` |
| `FileNotFoundError … smelter_sites.csv` | non-editable install | `pip install -e .` from the repository |
| `KeyError: 'site_id'` at the start of `so2-copper` | no `s5p_batch` files in `--data` | Run `s5p-batch` first, with the same `--out` folder as the `--data` given to `so2-copper` |
| ERA5 month never updates | a month downloaded while incomplete is cached forever (`era5/<YYYY-MM>.nc`) | Delete that file and rerun |

### 5.2 Planned (container / Kubernetes)

| Symptom | Likely cause | Check / fix |
|---|---|---|
| `ImagePullBackOff` | private GHCR package, or wrong tag | `kubectl describe pod`; make the package public or add `ghcr-pull` ([CI_CD.md §3](CI_CD.md#3-docker-publishyml--publish-to-ghcr-planned)) |
| `ErrImageNeverPull` / not found on Kind | image not loaded | `kind load docker-image orbitcu:dev --name orbitcu` |
| Pod `Pending`, PVC `Pending` | no default StorageClass | `kubectl get sc`; set `storageClassName` in the overlay |
| `Permission denied` under `/data` | volume not writable by UID 10001 | keep `fsGroup: 10001`; on hostPath/local-path, `chown 10001` the folder |
| `OOMKilled` | `s5p-batch` workers × memory | lower `S5P_WORKERS` or raise the limit |
| Job `DeadlineExceeded` | `activeDeadlineSeconds` reached (backfill-sized work in the daily job) | run a separate backfill; the next daily run resumes |
| Job `Complete` but map/data incomplete | exit code 0 on partial failure | read `/data/logs/<date>/gaps.txt` |
| No run at 04:00 | CronJob suspended, cluster off, or `startingDeadlineSeconds` missed | `kubectl get cronjob`; trigger by hand |
| `Read-only file system` | a library writes outside `/data` and `/tmp` | set `HOME=/tmp` in the ConfigMap |
| Corrupt Parquet after a kill | non-atomic write (audit A-7) | delete the file (the command downloads it again) or restore the previous blob version |
| `rclone` 403 | SAS expired or missing rights | create a new SAS and update the Secret |

## 6. Routine checks (planned)

- **Daily:** `kubectl -n orbitcu get jobs`; read `gaps.txt` if the last Job is not `Complete`.
- **Weekly:** `rclone size orbitcu:orbitcu-data` (should grow, never shrink);
  `kubectl -n orbitcu top pod` during a run.
- **Monthly:** check the SAS expiry date, update the image (merge → new
  `sha-` tag), and do a restore test into a scratch PVC.
