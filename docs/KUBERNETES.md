# OrbitCu — Kubernetes

> **Status: planned, not implemented.** The repository has **no Kubernetes
> manifests** yet (`infra/kubernetes/` does not exist). This document
> specifies them for Phase 7. No cluster has been created and nothing has
> been deployed.

## 1. Planned layout

```
infra/
├── kubernetes/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── namespace.yaml          Namespace orbitcu
│   │   ├── serviceaccount.yaml     ServiceAccount orbitcu-runner (no API token mounted)
│   │   ├── configmap.yaml          ConfigMap orbitcu-config (non-secret settings)
│   │   ├── storage.yaml            PVC orbitcu-data
│   │   ├── cronjob-daily.yaml      CronJob orbitcu-daily
│   │   ├── cronjob-weekly-images.yaml  CronJob orbitcu-weekly-images
│   │   ├── job-restore.yaml        Job template: object storage → PVC (suspended / applied by hand)
│   │   └── secret.example.yaml     Template only, with placeholders; NOT listed in kustomization
│   └── overlays/
│       ├── local/                  Kind: image orbitcu:dev, storageClass standard, small PVC
│       └── vm/                     k3s: GHCR image by SHA, storageClass local-path
└── scripts/
    └── daily_pipeline.sh           sequence of existing orbitcu commands (see OPERATIONS.md)
```

No Helm: the team needs two environments, and Kustomize overlays cover that
with plain YAML.

## 2. Resources

### Namespace and ServiceAccount
- Namespace `orbitcu`.
- ServiceAccount `orbitcu-runner` with `automountServiceAccountToken: false`.
  The jobs do not call the Kubernetes API, so no Role or RoleBinding is needed.

### ConfigMap `orbitcu-config` (non-secret)

| Key | Value | Used by |
|---|---|---|
| `DATA_ROOT` | `/data` | pipeline script |
| `S5P_WORKERS` | `2` | `s5p-batch --workers` |
| `FIRMS_WORKERS` | `4` | `statusmap --workers` |
| `IMAGE_WORKERS` | `8` | `image-scores --workers` |
| `CDSAPI_URL` | `https://cds.climate.copernicus.eu/api` | ERA5 (optional, this is the code default) |

### Secret `orbitcu-credentials` (never committed)
Keys: `FIRMS_MAP_KEY`, `CDSAPI_KEY`, and the rclone remote variables
(`RCLONE_CONFIG_ORBITCU_*`). Created with `kubectl create secret`. See
[SECURITY.md](SECURITY.md#3-runtime-secrets-kubernetes).

### PVC `orbitcu-data`
- `accessModes: [ReadWriteOnce]`, `storage: 50Gi` for the VM overlay
  (`5Gi` for local). The size is to be adjusted once the volumes have been measured.
- On Kind/k3s the `local-path` provisioner keeps data on the node disk. It
  survives pod deletion, but **not** node loss. Durable data are in object
  storage ([STORAGE.md](STORAGE.md)).
- Both CronJobs mount the same RWO PVC. This works on a single-node cluster.
  On a multi-node cluster, schedule both on the same node, or use RWX
  storage. The schedules do not overlap in time.

### CronJob `orbitcu-daily` (specification)

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: orbitcu-daily
  namespace: orbitcu
spec:
  schedule: "0 4 * * *"            # 04:00 every day
  timeZone: "Etc/UTC"              # explicit; requires Kubernetes >= 1.27
  concurrencyPolicy: Forbid        # never two runs at once (FIRMS quota, shared files)
  startingDeadlineSeconds: 3600    # if the controller was down, still start within 1 h
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  jobTemplate:
    spec:
      backoffLimit: 2              # up to 3 attempts; safe because commands resume
      activeDeadlineSeconds: 14400 # 4 h hard limit for the whole Job
      ttlSecondsAfterFinished: 604800
      template:
        spec:
          serviceAccountName: orbitcu-runner
          automountServiceAccountToken: false
          restartPolicy: Never     # each retry is a new pod, so logs are kept per attempt
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            fsGroup: 10001
            seccompProfile: { type: RuntimeDefault }
          containers:
            - name: pipeline
              image: ghcr.io/robindemclu/orbitcu:sha-REPLACE   # set by the Kustomize overlay
              command: ["/app/infra/scripts/daily_pipeline.sh"]
              envFrom:
                - configMapRef: { name: orbitcu-config }
                - secretRef:    { name: orbitcu-credentials }
              resources:
                requests: { cpu: "500m", memory: "1Gi" }
                limits:   { cpu: "2",    memory: "4Gi" }
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities: { drop: ["ALL"] }
              volumeMounts:
                - { name: data, mountPath: /data }
                - { name: tmp,  mountPath: /tmp }
          volumes:
            - name: data
              persistentVolumeClaim: { claimName: orbitcu-data }
            - name: tmp
              emptyDir: { sizeLimit: 2Gi }
```

Choices:
- **04:00 UTC.** VIIRS NRT is published about 3 h after the overpass
  (`ingest/pit.py`). Running in the early UTC morning picks up the previous
  night's passes for Europe and Asia. Sentinel-5P OFFL arrives with a delay of
  up to ~5 days, and `s5p-batch` redoes the last months for that reason.
- **`Forbid`.** A run that is still active blocks the next one. It does not
  queue it.
- **Memory.** `s5p-batch` uses one process per worker, and each keeps a 96 MB
  HDF5 chunk cache plus pixel tables. `--workers 2` in 4 GiB is the starting
  point. Measure with `kubectl top pod` and adjust.
- **`readOnlyRootFilesystem`.** The code writes only under `--out` paths
  (`/data`) and to `/tmp`. Check this during Phase 7; if a library writes
  elsewhere (e.g. `~/.cache`), set `HOME=/tmp`.

### CronJob `orbitcu-weekly-images`
Same template, with these differences:
- `schedule: "0 6 * * 0"` (Sunday 06:00 UTC, after the daily run).
- `activeDeadlineSeconds: 10800`.
- The command:

```
orbitcu image-scores --start-year <current year> --end-year <current year> --out /data/processed/image_scores --workers 8
```

It is wrapped in a two-line script so that the year is computed at run time.
On the first days of January it also re-scores the previous year, which is
redone until 31 January.

### Restore Job (`job-restore.yaml`)
Runs `rclone copy orbitcu:orbitcu-data /data --checksum` once. It is applied
by hand after a new cluster or PVC is created. See [OPERATIONS.md](OPERATIONS.md#4-runbooks).

## 3. Why one sequential pipeline per schedule

- `so2-copper` depends on `s5p-batch` (and on `era5_ts/`).
- `statusmap` and any other FIRMS work share one key quota.
- The resumable commands make a retried Job cheap: finished months are skipped.

Separate CronJobs per source would be possible for the three independent
downloaders. They would need ordering for `so2-copper`, which CronJobs
cannot express. Argo Workflows can, and is deferred; see
[ARCHITECTURE.md §4 D1](ARCHITECTURE.md#d1-cronjobs-not-argo-workflows-for-now).

## 4. Planned commands

Local cluster with Kind (requires Docker, kind and kubectl):

```bash
kind create cluster --name orbitcu
```

```bash
docker build -t orbitcu:dev .
```

```bash
kind load docker-image orbitcu:dev --name orbitcu
```

```bash
kubectl apply -f infra/kubernetes/base/namespace.yaml
```

```bash
kubectl -n orbitcu create secret generic orbitcu-credentials \
  --from-literal=FIRMS_MAP_KEY="$FIRMS_MAP_KEY" \
  --from-literal=CDSAPI_KEY="$CDSAPI_KEY"
```

```bash
kubectl apply -k infra/kubernetes/overlays/local
```

Trigger a run now instead of waiting for 04:00 UTC:

```bash
kubectl -n orbitcu create job --from=cronjob/orbitcu-daily orbitcu-daily-manual-1
```

```bash
kubectl -n orbitcu logs -f job/orbitcu-daily-manual-1
```

Validation without a cluster (also run in CI):

```bash
kubectl kustomize infra/kubernetes/overlays/local > /tmp/orbitcu-local.yaml
```

```bash
kubeconform -strict -summary /tmp/orbitcu-local.yaml
```

```bash
kubectl apply --dry-run=server -k infra/kubernetes/overlays/local
```

The last command needs a running cluster (Kind).

## 5. Observability

| Question | Command |
|---|---|
| When did it last run? | `kubectl -n orbitcu get cronjob orbitcu-daily` (`LAST SCHEDULE`) |
| Did it succeed? | `kubectl -n orbitcu get jobs` (`COMPLETIONS`), `kubectl -n orbitcu describe job <name>` |
| What happened? | `kubectl -n orbitcu logs job/<name>`; for a retried Job: `kubectl -n orbitcu get pods -l job-name=<name>` then `kubectl logs <pod>` |
| Resource use | `kubectl -n orbitcu top pod` (needs metrics-server; k3s ships it, Kind does not) |

Pod logs disappear with the pod (after `ttlSecondsAfterFinished`). The
pipeline script therefore also writes `/data/logs/<date>/<step>.log`, which
is pushed to object storage with the data.

**Job success ≠ complete data.** `statusmap` and `s5p-batch` exit with code 0
when they skip months (audit A-5). The pipeline script searches the logs for
`SKIPPED`, `RETRY LATER` and `could not be downloaded`, and exits non-zero
(code 3) when it finds any, **after** pushing what succeeded. See
[OPERATIONS.md](OPERATIONS.md).

## 6. Security settings summary
- Non-root UID 10001, no privilege escalation, all capabilities dropped,
  RuntimeDefault seccomp, read-only root filesystem.
- No ServiceAccount token, no RBAC.
- Credentials only in a Secret, injected as environment variables. They are
  never put in the manifests in Git.
- Optional NetworkPolicy: egress only to DNS and 443. Kind's default CNI does
  not enforce NetworkPolicy, so this is documented, not relied upon.

See [SECURITY.md](SECURITY.md).
