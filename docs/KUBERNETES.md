# OrbitCu — Kubernetes

> **Status: planned. There is no `infra/` directory and no Kubernetes
> manifest in the repository yet.** The manifests below are proposals for
> `infra/kubernetes/`. They were parsed and checked with a structural
> validation script (§6). They have **not** been applied to a cluster or
> checked against the Kubernetes OpenAPI schema; that will happen in CI
> ([CI_CD.md](CI_CD.md)) and during the Kind validation
> ([OPERATIONS.md](OPERATIONS.md), §5).

## 1. Layout

```
infra/
  kubernetes/
    kustomization.yaml      # kubectl apply -k infra/kubernetes
    namespace.yaml          # namespace orbitcu
    serviceaccount.yaml     # orbitcu-runner, no API token mounted
    configmap.yaml          # orbitcu-config: non-secret settings
    storage.yaml            # PVC orbitcu-work (working copy, /work)
    cronjob.yaml            # orbitcu-daily: restore + daily pipeline
    upload-cronjob.yaml     # orbitcu-upload: copy /work -> object storage
    backfill-job.yaml       # manual one-off Job (NOT in kustomization)
    secret.example.yaml     # placeholders only (NOT in kustomization)
  scripts/
    daily.sh                # sequential pipeline calling the orbitcu CLI
```

Plain YAML and Kustomize (built into `kubectl`). No Helm chart: there is one
application, two schedules and no per-environment templating that would
justify one.

## 2. Why one daily CronJob (and not one CronJob per source)

The audit ([INFRASTRUCTURE_AUDIT.md](INFRASTRUCTURE_AUDIT.md), §9) shows:

* All steps write into the same `output/` and `cache/` trees, and the code
  has **no locking**.
* `so2-copper` depends on `s5p-batch` and on ERA5 time series that no CLI
  command produces yet.
* FIRMS has one quota per key.

Separate concurrent CronJobs would need either several PVCs (one per writer)
or ReadWriteMany storage, plus ordering between them. A single sequential
Job is simpler, avoids write conflicts and is cheap to retry, because each
command skips finished work. Argo Workflows is the upgrade path when the
pipeline grows ([ARCHITECTURE.md](ARCHITECTURE.md), §3.4).

## 3. Manifests (proposed)

### `namespace.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: orbitcu
  labels:
    app.kubernetes.io/part-of: orbitcu
```

### `serviceaccount.yaml`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: orbitcu-runner
automountServiceAccountToken: false   # the jobs never call the Kubernetes API
```

### `configmap.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: orbitcu-config
data:
  ORBITCU_WORKDIR: /work
  ORBITCU_STATUSMAP_MONTHS: "12"
  ORBITCU_S5P_WORKERS: "4"
  ORBITCU_FIRMS_WORKERS: "4"
  ORBITCU_IMAGE_SCORES_WORKERS: "8"
  ORBITCU_RUN_IMAGE_SCORES: "weekly"   # daily | weekly | never
  RCLONE_REMOTE: "orbitcu-blob:orbitcu"
```

These variables are read **only by `infra/scripts/daily.sh`**, which turns
them into CLI options. The `orbitcu` package itself reads no `ORBITCU_*`
variable.

### `storage.yaml`

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: orbitcu-work
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 20Gi
  # storageClassName omitted: cluster default
  # (Kind: "standard" local-path; AKS: "default" Azure Disk)
```

The PVC is a **working copy**. The durable copy is object storage
([STORAGE.md](STORAGE.md)). Size it after measuring the real data volume
(`du -sh output cache` on a team laptop).

### `cronjob.yaml`

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: orbitcu-daily
spec:
  schedule: "30 6 * * *"          # 06:30 UTC every day
  timeZone: "Etc/UTC"             # needs Kubernetes >= 1.27
  concurrencyPolicy: Forbid       # never two runs at once
  startingDeadlineSeconds: 3600   # skip the run if it cannot start within 1 h
  successfulJobsHistoryLimit: 7
  failedJobsHistoryLimit: 7
  jobTemplate:
    spec:
      backoffLimit: 2             # up to 3 attempts in total
      activeDeadlineSeconds: 14400  # 4 h hard limit per Job
      ttlSecondsAfterFinished: 1209600  # keep finished Jobs (and logs) 14 days
      template:
        metadata:
          labels:
            app.kubernetes.io/name: orbitcu
            app.kubernetes.io/component: daily
        spec:
          serviceAccountName: orbitcu-runner
          restartPolicy: Never
          terminationGracePeriodSeconds: 120
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            fsGroup: 10001
            seccompProfile:
              type: RuntimeDefault
          initContainers:
            - name: restore
              image: rclone/rclone:1.68.2
              # copy only files missing on the PVC; never deletes anything
              args: ["copy", "--ignore-existing", "--config", "/config/rclone.conf",
                     "$(RCLONE_REMOTE)", "/work", "--log-level", "INFO"]
              envFrom:
                - configMapRef: {name: orbitcu-config}
              env:
                - {name: HOME, value: /tmp}
              volumeMounts:
                - {name: work, mountPath: /work}
                - {name: tmp, mountPath: /tmp}
                - {name: rclone-config, mountPath: /config, readOnly: true}
              resources:
                requests: {cpu: 100m, memory: 128Mi}
                limits: {cpu: 500m, memory: 512Mi}
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities: {drop: ["ALL"]}
          containers:
            - name: pipeline
              image: ghcr.io/robindemclu/orbitcu:REPLACE_WITH_GIT_SHA
              imagePullPolicy: IfNotPresent
              command: ["/usr/bin/tini", "--", "/bin/bash", "/app/infra/scripts/daily.sh"]
              envFrom:
                - configMapRef: {name: orbitcu-config}
                - secretRef: {name: orbitcu-credentials}
              env:
                - {name: HOME, value: /tmp}
              volumeMounts:
                - {name: work, mountPath: /work}
                - {name: tmp, mountPath: /tmp}
              resources:
                requests: {cpu: 500m, memory: 1Gi}
                limits: {cpu: "2", memory: 4Gi}
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities: {drop: ["ALL"]}
          volumes:
            - name: work
              persistentVolumeClaim: {claimName: orbitcu-work}
            - name: tmp
              emptyDir: {sizeLimit: 2Gi}
            - name: rclone-config
              secret: {secretName: orbitcu-storage}
```

### `upload-cronjob.yaml`

The application image does not contain rclone, and a Job has no "after the
main container" hook. The upload is therefore a **second CronJob**,
scheduled after the daily Job's worst-case end (06:30, plus 1 h of start
delay, plus the 4 h deadline, gives 11:30 at the latest). It also runs on
days when the pipeline failed, so months that finished before the failure
are still saved.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: orbitcu-upload
spec:
  schedule: "0 12 * * *"          # 12:00 UTC, after orbitcu-daily's deadline
  timeZone: "Etc/UTC"
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 3600
  successfulJobsHistoryLimit: 7
  failedJobsHistoryLimit: 7
  jobTemplate:
    spec:
      backoffLimit: 3
      activeDeadlineSeconds: 7200
      ttlSecondsAfterFinished: 1209600
      template:
        metadata:
          labels:
            app.kubernetes.io/name: orbitcu
            app.kubernetes.io/component: upload
        spec:
          serviceAccountName: orbitcu-runner
          restartPolicy: Never
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            fsGroup: 10001
            seccompProfile:
              type: RuntimeDefault
          containers:
            - name: upload
              image: rclone/rclone:1.68.2
              # "copy" never deletes on the destination; *.tmp = unfinished writes
              args: ["copy", "--config", "/config/rclone.conf", "/work", "$(RCLONE_REMOTE)",
                     "--exclude", "*.tmp", "--log-level", "INFO", "--stats-one-line"]
              envFrom:
                - configMapRef: {name: orbitcu-config}
              env:
                - {name: HOME, value: /tmp}
              volumeMounts:
                - {name: work, mountPath: /work, readOnly: true}
                - {name: tmp, mountPath: /tmp}
                - {name: rclone-config, mountPath: /config, readOnly: true}
              resources:
                requests: {cpu: 100m, memory: 128Mi}
                limits: {cpu: "1", memory: 512Mi}
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities: {drop: ["ALL"]}
          volumes:
            - name: work
              persistentVolumeClaim: {claimName: orbitcu-work}
            - name: tmp
              emptyDir: {sizeLimit: 1Gi}
            - name: rclone-config
              secret: {secretName: orbitcu-storage}
```

With a `ReadWriteOnce` PVC, both CronJobs must run on the **same node**.
That is always true on Kind and single-node k3s. On a multi-node cluster,
add a pod affinity rule or use a ReadWriteMany storage class.

### `backfill-job.yaml` (manual, not scheduled)

```yaml
# Manual historical backfill. Not listed in kustomization.yaml.
# Edit args, then: kubectl -n orbitcu create -f infra/kubernetes/backfill-job.yaml
# Do not run while orbitcu-daily or orbitcu-upload is active (same PVC, no locking).
apiVersion: batch/v1
kind: Job
metadata:
  generateName: orbitcu-backfill-
  namespace: orbitcu
  labels:
    app.kubernetes.io/name: orbitcu
    app.kubernetes.io/component: backfill
spec:
  backoffLimit: 3
  activeDeadlineSeconds: 86400
  ttlSecondsAfterFinished: 1209600
  template:
    spec:
      serviceAccountName: orbitcu-runner
      restartPolicy: Never
      terminationGracePeriodSeconds: 120
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: backfill
          image: ghcr.io/robindemclu/orbitcu:REPLACE_WITH_GIT_SHA
          # Example: Sentinel-5P months 2022-01..2026-10 (resumable, finished months skipped)
          args: ["s5p-batch", "--start", "2022-01", "--end", "2026-10", "--out", "output", "--workers", "4"]
          envFrom:
            - secretRef: {name: orbitcu-credentials}
          env:
            - {name: HOME, value: /tmp}
          volumeMounts:
            - {name: work, mountPath: /work}
            - {name: tmp, mountPath: /tmp}
          resources:
            requests: {cpu: "1", memory: 2Gi}
            limits: {cpu: "4", memory: 8Gi}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: ["ALL"]}
      volumes:
        - name: work
          persistentVolumeClaim: {claimName: orbitcu-work}
        - name: tmp
          emptyDir: {sizeLimit: 4Gi}
```

Other backfills use the same template with different `args`, for example
`["image-scores", "--start-year", "2018", "--end-year", "2025", "--out", "output/image_scores"]`
or `["statusmap", "--start", "2019-01", "--end", "2026-10", "--out", "output/status_map.html"]`.
Run the upload CronJob by hand afterwards (§5).

### `kustomization.yaml`

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: orbitcu
labels:
  - pairs:
      app.kubernetes.io/part-of: orbitcu
    includeSelectors: false
resources:
  - namespace.yaml
  - serviceaccount.yaml
  - configmap.yaml
  - storage.yaml
  - cronjob.yaml
  - upload-cronjob.yaml
images:
  - name: ghcr.io/robindemclu/orbitcu
    newTag: REPLACE_WITH_GIT_SHA   # set per deploy: kustomize edit set image ...
```

The Secrets are **not** in the kustomization. They are created by hand
([SECURITY.md](SECURITY.md)). `secret.example.yaml` holds placeholders only.

## 4. Settings explained

| Setting | Value | Why |
|---|---|---|
| `schedule` / `timeZone` | `30 6 * * *`, `Etc/UTC` | 06:30 UTC is after the NRT products of the night are published (FIRMS NRT is about 3 h behind) and before the European working day. Every timestamp in OrbitCu is UTC. |
| `concurrencyPolicy` | `Forbid` | The CLI has no locking, so overlapping runs could corrupt caches. |
| `startingDeadlineSeconds` | 3600 | A run delayed more than 1 h (for example a laptop asleep on Kind) is skipped rather than started late, which keeps the upload schedule safe. |
| `backoffLimit` | 2 (daily), 3 (upload, backfill) | Transient API errors are common. Retries are cheap because the steps are incremental. |
| `activeDeadlineSeconds` | 4 h (daily), 2 h (upload), 24 h (backfill) | Stops hung downloads. Note that `image-scores` on a full year can take hours (the report quotes about 3 h for 2018–2026 on a laptop). |
| `restartPolicy` | `Never` | A failed attempt keeps its pod and logs; the Job creates a new pod for the retry. |
| `ttlSecondsAfterFinished` | 14 days | Keeps Job objects and their logs inspectable for two weeks. |
| Requests / limits | See the manifests | First estimates, **not measured**. Adjust after the first Kind runs (`kubectl top pod` needs metrics-server). `s5p-batch` uses worker **processes**, so memory grows with `--workers`. |
| Security | Non-root 10001, no privilege escalation, all capabilities dropped, read-only root filesystem, `RuntimeDefault` seccomp, no API token | Least privilege. The read-only root filesystem was tested with Docker ([DOCKER.md](DOCKER.md), §5). |
| Image pull | GHCR | If the GHCR package is **public**, no pull secret is needed. If it is private, see §5.4. |

## 5. Commands

### 5.1 Local cluster with Kind

```bash
kind create cluster --name orbitcu
```

```bash
docker build -t ghcr.io/robindemclu/orbitcu:dev .
```

```bash
kind load docker-image ghcr.io/robindemclu/orbitcu:dev --name orbitcu
```

### 5.2 Deploy

Create the namespace first, so the Secrets can be created before the
CronJobs run:

```bash
kubectl apply -f infra/kubernetes/namespace.yaml
```

```bash
kubectl -n orbitcu create secret generic orbitcu-credentials --from-env-file=.env.k8s
```

```bash
kubectl -n orbitcu create secret generic orbitcu-storage --from-file=rclone.conf=./rclone.conf
```

Choose the image tag (here the local `dev` build; in production use a commit
SHA published by CI). Run this from `infra/kubernetes/`. Note that it
modifies `kustomization.yaml`:

```bash
kustomize edit set image ghcr.io/robindemclu/orbitcu=ghcr.io/robindemclu/orbitcu:dev
```

Render and check without applying:

```bash
kubectl kustomize infra/kubernetes
```

```bash
kubectl apply -k infra/kubernetes --dry-run=server
```

Apply:

```bash
kubectl apply -k infra/kubernetes
```

### 5.3 Run now, watch and inspect

```bash
kubectl -n orbitcu create job --from=cronjob/orbitcu-daily orbitcu-daily-manual-1
```

```bash
kubectl -n orbitcu get cronjobs,jobs,pods
```

```bash
kubectl -n orbitcu logs -f job/orbitcu-daily-manual-1 -c pipeline
```

```bash
kubectl -n orbitcu logs job/orbitcu-daily-manual-1 -c restore
```

```bash
kubectl -n orbitcu create job --from=cronjob/orbitcu-upload orbitcu-upload-manual-1
```

Pause and resume the schedule:

```bash
kubectl -n orbitcu patch cronjob orbitcu-daily -p '{"spec":{"suspend":true}}'
```

```bash
kubectl -n orbitcu patch cronjob orbitcu-daily -p '{"spec":{"suspend":false}}'
```

### 5.4 Private GHCR image (only if the package is not public)

Create a GitHub *fine-grained* or classic token with only `read:packages`,
then:

```bash
kubectl -n orbitcu create secret docker-registry ghcr-pull --docker-server=ghcr.io --docker-username=<github-user> --docker-password=<token-with-read-packages>
```

Add the secret to the ServiceAccount:

```yaml
imagePullSecrets:
  - name: ghcr-pull
```

## 6. Validation done so far

| Check | Result |
|---|---|
| YAML parses (PyYAML 6.0.3) | OK, all files |
| Every `resources:` entry in the kustomization exists | OK |
| CronJobs: 5-field schedule, `Etc/UTC`, `Forbid`, `backoffLimit >= 1`, `activeDeadlineSeconds > 0`, `restartPolicy: Never`, `runAsNonRoot` | OK (`orbitcu-daily` 06:30, `orbitcu-upload` 12:00) |
| Every container has requests and limits, with `allowPrivilegeEscalation: false` | OK |
| Every `volumeMount` refers to a declared volume | OK |
| `$(RCLONE_REMOTE)` in args is defined in the ConfigMap | OK |
| ConfigMap values are strings | OK |
| `kubectl kustomize`, `kubeconform` schema check | **Not run** (these binaries could not be run in the authoring sandbox). Planned in CI. |
| `kubectl apply --dry-run=server` on Kind | **Not run**, planned in [OPERATIONS.md](OPERATIONS.md) §5 |
| `daily.sh` | Syntax OK. The dry run prints the expected commands. A missing key gives exit 1. SIGTERM gives a clean exit 143. Tested in the locally built image. |
