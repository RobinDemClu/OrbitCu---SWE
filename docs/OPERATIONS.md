# OrbitCu — Operations: Daily Run, Validation, Troubleshooting, Recovery

> **Status:** the daily automation is **planned and not deployed**. Today,
> every run is started by hand with the `orbitcu` CLI. The pipeline script
> in §2 was tested in a locally built image in dry-run mode, with a missing
> key and with SIGTERM. It has **never run against the real data APIs or on
> a cluster**.

## 1. Daily schedule (planned)

| Time (UTC) | Object | What |
|---|---|---|
| 06:30 | CronJob `orbitcu-daily` | Restore missing files from Blob, then run `infra/scripts/daily.sh` (4 h limit, up to 3 attempts) |
| 12:00 | CronJob `orbitcu-upload` | `rclone copy /work → Blob` (never deletes) |
| On demand | Job from `backfill-job.yaml` | Historical backfill (never scheduled) |

All times are UTC (`timeZone: Etc/UTC`), the same time base as every
timestamp in the data (`acquired_at`, `available_at`). Convert them to local
time when reading `kubectl` output. For example, in Central Europe 06:30 UTC
is 07:30 in winter and 08:30 in summer.

## 2. Daily pipeline script (proposed `infra/scripts/daily.sh`)

The script uses only existing commands with options verified against
`--help`. It is sequential because the steps share storage and the code has
no locking.

```bash
#!/usr/bin/env bash
# OrbitCu daily pipeline. Calls only existing CLI commands.
# Every step is incremental: finished months/years already on disk are skipped by the CLI.
set -Eeuo pipefail

cd "${ORBITCU_WORKDIR:-/work}"
mkdir -p logs output cache
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/daily-${RUN_ID}.log"
exec > >(tee -a "$LOG") 2>&1

child=0
on_term() {
  echo "[daily] termination signal received, stopping current step"
  if [ "$child" -ne 0 ]; then kill -TERM "$child" 2>/dev/null || true; wait "$child" || true; fi
  exit 143
}
trap on_term TERM INT

run() {
  echo "[daily] $(date -u +%H:%M:%S) + orbitcu $*"
  if [ "${DRY_RUN:-0}" = "1" ]; then return 0; fi
  orbitcu "$@" &
  child=$!
  local rc=0
  wait "$child" || rc=$?
  child=0
  return "$rc"
}

month_start="$(date -u +%Y-%m-01)"
cur="$(date -u +%Y-%m)"
prev="$(date -u -d "${month_start} -1 month" +%Y-%m)"
first="$(date -u -d "${month_start} -$(( ${ORBITCU_STATUSMAP_MONTHS:-12} - 1 )) month" +%Y-%m)"
day="$(date -u +%-d)"
dow="$(date -u +%u)"   # 1 = Monday
year="$(date -u +%Y)"
fw="${ORBITCU_FIRMS_WORKERS:-4}"

echo "[daily] run ${RUN_ID}: current month ${cur}, previous ${prev}"

# 1. VIIRS (FIRMS). statusmap never refreshes a past month once cached, so early in a
#    month re-download the previous month to pick up its last hours.
if [ "$day" -le 5 ]; then
  run statusmap --start "$prev" --end "$prev" --refresh --firms-dir cache/firms_hist \
      --out /tmp/refresh_status_map.html --workers "$fw"
fi
run statusmap --start "$first" --end "$cur" --firms-dir cache/firms_hist \
    --out output/status_map.html --json output/status_map.json --workers "$fw"

# 2. Sentinel-5P SO2 (unfinished months are redone, finished ones skipped)
run s5p-batch --start "$prev" --end "$cur" --out output --workers "${ORBITCU_S5P_WORKERS:-4}"

# 3. Sentinel-2 / Landsat heat scores: the current year is rescored on every call (costly)
case "${ORBITCU_RUN_IMAGE_SCORES:-weekly}" in
  daily) do_img=1 ;;
  weekly) [ "$dow" = "1" ] && do_img=1 || do_img=0 ;;
  *) do_img=0 ;;
esac
if [ "$do_img" = "1" ]; then
  start_year="$year"
  [ "$(date -u +%m)" = "01" ] && start_year=$(( year - 1 ))   # previous year is open until 31 Jan
  run image-scores --start-year "$start_year" --end-year "$year" --out output/image_scores \
      --workers "${ORBITCU_IMAGE_SCORES_WORKERS:-8}"
fi

# 4. Some commands print partial failures but exit 0: make them visible as a failed Job.
if grep -Eq "SKIPPED|RETRY LATER|could not be downloaded|orbits failed" "$LOG"; then
  echo "[daily] PARTIAL FAILURE: see lines above; the next run (or a Job retry) fills the gaps"
  exit 3
fi
echo "[daily] $(date -u +%H:%M:%S) done"
```

### Why each step is there, and what is left out

| Step | Reason |
|---|---|
| `statusmap` with a 12-month window ending in the current month | Incremental FIRMS cache (`cache/firms_hist`). Only missing months and the current month are downloaded. It also rebuilds the FINAL status map. |
| `statusmap --refresh` on the previous month (days 1–5) | `statusmap` skips any **past** month already on disk. Without this step, the previous month would keep the copy taken on its last day and miss its final hours. |
| `s5p-batch` previous and current month | `s5p-batch` redoes a month until 5 days after its end, which covers late OFFL products. |
| `image-scores` weekly | Each call re-scores the **whole current year**: cost grows through the year (audit R9). Set `ORBITCU_RUN_IMAGE_SCORES=daily` if the team accepts the cost. |
| Log scan → exit 3 | `s5p-batch`, `statusmap` and `image-scores` print partial failures but exit 0 (audit R3). Exit 3 makes the Job fail, so the failure is visible and retried. |
| **Not included:** `so2-copper` | Needs `output/era5_ts/`, which no CLI command creates (audit R7). |
| **Not included:** `firms`, `s5p`, `optical`, `era5` | They overwrite per-window files or never refresh months (audit R5/R6). Use them by hand or in backfills. |
| **Not included:** fusion, indices, copper estimate, trading, dashboard | No CLI command exists yet. Add them as script steps when they do. |

### Test results of the script (27 November 2026, local image, no cluster)

* `bash -n`: syntax OK.
* `DRY_RUN=1 ORBITCU_RUN_IMAGE_SCORES=daily`: printed `statusmap --start 2025-12
  --end 2026-11 ...`, `s5p-batch --start 2026-10 --end 2026-11 ...` and
  `image-scores --start-year 2026 --end-year 2026 ...`, then exit 0.
* No `FIRMS_MAP_KEY`: `RuntimeError: FIRMS_MAP_KEY is not set`, script exit 1,
  and the log file was written to `logs/`.
* `docker stop` during a step: "termination signal received", exit 143, with
  no wait for SIGKILL.
* **Not tested:** the days 1–5 refresh branch (the test date was 27 November),
  and real downloads.

## 3. Routine operations

| Task | Command |
|---|---|
| Status of schedules and runs | `kubectl -n orbitcu get cronjobs,jobs,pods` |
| Logs of the last daily run | `kubectl -n orbitcu logs job/<job-name> -c pipeline` |
| Logs of the restore step | `kubectl -n orbitcu logs job/<job-name> -c restore` |
| Why a pod failed | `kubectl -n orbitcu describe pod <pod>` |
| Run the pipeline now | `kubectl -n orbitcu create job --from=cronjob/orbitcu-daily manual-$(date +%s)` |
| Upload now | `kubectl -n orbitcu create job --from=cronjob/orbitcu-upload upload-$(date +%s)` |
| Pause the schedule | `kubectl -n orbitcu patch cronjob orbitcu-daily -p '{"spec":{"suspend":true}}'` |
| Old logs (after the Job TTL) | `rclone ls orbitcu-blob:orbitcu/logs/` (logs are uploaded with the data) |
| Disk use on the PVC | Debug pod (below) |
| Deploy a new image | [CI_CD.md](CI_CD.md), §6 |

Debug pod that mounts the PVC and prints its usage. Run it only when no Job
is running, because the PVC is `ReadWriteOnce`. Change the `command` to
`["ls","-R","/work/cache"]` to list files.

```bash
kubectl -n orbitcu run pvc-debug --rm -it --restart=Never --image=busybox:1.36 --overrides='{"spec":{"securityContext":{"runAsUser":10001},"volumes":[{"name":"w","persistentVolumeClaim":{"claimName":"orbitcu-work"}}],"containers":[{"name":"pvc-debug","image":"busybox:1.36","command":["du","-sh","/work/output","/work/cache"],"volumeMounts":[{"name":"w","mountPath":"/work"}]}]}}'
```

**Monitoring:** there is no alerting yet. Minimum routine: one team member
checks `kubectl get jobs` every morning. A later improvement could be a
failure notification (for example a small CronJob that posts failed Jobs to
a team chat webhook).

## 4. Historical backfills

1. Suspend `orbitcu-daily` (the two jobs share the PVC and the code has no
   locking).
2. Edit the `args` in `infra/kubernetes/backfill-job.yaml`, for example
   `s5p-batch --start 2022-01 --end 2026-10`, or `image-scores --start-year
   2018 --end-year 2025`, or `statusmap --start 2019-01 --end 2026-10`.
3. Create the Job:

   ```bash
   kubectl -n orbitcu create -f infra/kubernetes/backfill-job.yaml
   ```

4. Follow the logs, then run the upload Job by hand, then resume the daily
   CronJob.

All three commands are resumable. If the Job hits its deadline or fails,
create it again and it continues where it stopped.

## 5. Validation plan (Phase 10, to run on Kind once implemented)

None of these steps has been run yet. Record the results in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

| # | Test | How | Expected |
|---|---|---|---|
| 1 | Small download | Backfill Job with args `["statusmap","--start","2026-10","--end","2026-10","--out","output/status_map.html"]` | `cache/firms_hist/<site>/2026-10.parquet` for 29 sites |
| 2 | Present in durable storage | Run the upload Job, then `rclone ls orbitcu-blob:orbitcu/cache/firms_hist/ \| wc -l` | 29 files |
| 3 | Survives pod deletion | `kubectl -n orbitcu delete pod -l app.kubernetes.io/component=backfill`, then list the PVC (debug pod, §3) and Blob | Files still there |
| 4 | Survives PVC loss | `kubectl -n orbitcu delete pvc orbitcu-work`, `kubectl apply -k infra/kubernetes`, run the daily Job | The restore log shows files copied; 29 files back on the PVC |
| 5 | No duplicate download | Repeat test 1 | Log: `VIIRS history: everything already cached` |
| 6 | Failure is visible | Recreate `orbitcu-credentials` with a wrong `FIRMS_MAP_KEY` and run the daily Job | Job `Failed` after 3 attempts; logs show the FIRMS error |
| 7 | Recovery | Fix the Secret and rerun | Job `Complete`; gaps filled |
| 8 | Logs accessible | `kubectl logs` during the TTL; `logs/` in Blob afterwards | Both readable |
| 9 | No overlap | Start two manual Jobs from the CronJob at once | Note: `concurrencyPolicy` applies only to **scheduled** runs; manual `create job` is not blocked, so operators must not start two by hand |
| 10 | No data deleted | `rclone size orbitcu-blob:orbitcu` before and after tests 3–7 | Object count never decreases |
| 11 | Schedule | `kubectl -n orbitcu get cronjob orbitcu-daily -o jsonpath='{.spec.schedule} {.spec.timeZone}'` | `30 6 * * * Etc/UTC` |

## 6. Recovery runbooks

**Job failed.** Read the logs (§3). If the cause was transient (quota,
network, provider outage), do nothing: the next daily run fills the gaps. If
it was the configuration (key, Secret, image), fix it and start a manual Job.

**Pod stuck in `Pending`.** `kubectl describe pod`. Usual causes: the PVC is
not bound (no default storage class), or the cluster lacks the CPU or memory
for the requests.

**`ImagePullBackOff`.** Wrong tag, or the GHCR package is private. Check the
tag in `kustomization.yaml` and the package visibility, or add a pull secret
([KUBERNETES.md](KUBERNETES.md), §5.4).

**Node or PVC lost.** Run `kubectl apply -k infra/kubernetes` (this recreates
the PVC), then start a manual daily Job. The `restore` init container copies
everything back from Blob. The first run after the loss is slow (a full
download from Blob).

**Whole cluster lost.**

1. Create a new cluster.
2. Create the namespace.
3. Recreate the two Secrets from your local files.
4. Run `kubectl apply -k infra/kubernetes`.
5. Start a manual daily Job.

No data is lost if the upload ran. At most one day of downloads (since the
last upload) has to be fetched again, and the CLI does that automatically.

**Corrupt Parquet file** (error reading a cache file). Delete only that file
on the PVC. The next run downloads it again. If the corrupt version was
already uploaded, restore the previous blob version (portal → blob →
Versions).

**Blob deleted by mistake.** Undelete it within the 30-day soft-delete window
(portal, or `az storage blob undelete`). After that window, the data is gone:
**there is no second backup yet**.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: .../data/registry/smelter_sites.csv` | Package installed non-editable (`REPO_ROOT` points into site-packages) | `pip install -e .` from the checkout; in Docker use the documented Dockerfile |
| `ModuleNotFoundError: No module named 'scipy'` when running `pytest` | Only `.[dev]` installed | `pip install -e ".[dev,era5,s5p,emissions]"` |
| `ImportError: libexpat.so.1` in a slim container | Missing system library for the rasterio wheel | `apt-get install libexpat1` (in the documented Dockerfile) |
| `RuntimeError: FIRMS_MAP_KEY is not set` | Key missing in the shell, `.env` or Secret | Set it ([SECURITY.md](SECURITY.md)) |
| `KeyError: 'CDSAPI_KEY'` | Same, for ERA5 | Set `CDSAPI_KEY` and accept the ERA5 licence |
| FIRMS answers "Invalid MAP_KEY" or "Exceeding allowed transaction limit" | 5000 transactions per 10 minutes exceeded | The client already pauses and retries; lower `--workers`; never run two FIRMS jobs with the same key |
| `SKIPPED 2026-10: ...` from `s5p-batch` | Catalogue or network error, or failed orbits | Rerun; the month is retried. The daily script fails the Job (exit 3) so this is noticed |
| `RETRY LATER <site> <sensor> <year>` | More than 20 % of image fetches failed | Rerun later; the year is not saved until it succeeds |
| `N site-months could not be downloaded` | FIRMS quota or network | Run the same command again |
| Job killed at exactly 4 h | `activeDeadlineSeconds` | Usually `image-scores`: run it as a backfill Job or raise the deadline |
| `OOMKilled` | Memory limit too low (`s5p-batch` worker processes) | Lower `ORBITCU_S5P_WORKERS` or raise the limit |
| `statusmap --open` does nothing in a container | No browser in a container | Open `output/status_map.html` on the host |
| Permission denied on `/work` with `docker run -v` (Linux) | Container UID 10001 | `chown 10001:10001 work` or `--user "$(id -u):$(id -g)"` |
| CronJob never fires on Kind | Laptop asleep or Docker stopped; with `startingDeadlineSeconds: 3600` late runs are skipped | Start a manual Job; for unattended runs use an always-on host |
