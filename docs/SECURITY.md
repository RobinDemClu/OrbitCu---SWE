# OrbitCu — Security and Secrets

> **Status:** §1 (current practice) describes the repository as it is. The
> other sections are the **planned** configuration for Docker, Kubernetes,
> GitHub Actions and object storage, none of which exist yet.

## 1. Current state (verified 27 Nov 2026)

- Credentials are read from environment variables only: `FIRMS_MAP_KEY` in
  `src/orbitcu/ingest/firms.py`, `CDSAPI_KEY` and `CDSAPI_URL` in
  `src/orbitcu/ingest/era5.py`.
- `.gitignore` ignores `.env`, `/output/` and `/cache/`. Downloaded satellite
  data and keys are therefore not committed by default.
- The FIRMS client never prints the request URL, which contains the key. On
  final failure it raises a generic `RuntimeError`.
- A search of the tracked files for `MAP_KEY=`, `CDSAPI_KEY=`, `AccountKey=`
  and `sig=` found no real credential. The README uses `...` placeholders,
  and the only `sig=abc` is a dummy value in `tests/test_s5p.py`.
- No CI, no container and no cluster exist, so there are no stored GitHub,
  registry or cluster secrets.

Residual risks in the code (not changed by infrastructure):

| Risk | Detail | Mitigation |
|---|---|---|
| Key in exception text | `firms._get` catches `ConnectionError`/`Timeout`. Another `requests` exception (e.g. `InvalidURL`) would propagate, with a message that can contain the URL and therefore the key | Low probability. The logs are private to the namespace and the bucket. Later, catch `requests.RequestException` in `_get` |
| Key in shell history | `export FIRMS_MAP_KEY=...` | Use a `.env` file, or `read -s FIRMS_MAP_KEY; export FIRMS_MAP_KEY` |

## 2. Credential inventory

| Secret | Scope | Where it lives | Who needs it |
|---|---|---|---|
| `FIRMS_MAP_KEY` | NASA FIRMS, free, rate-limited | developer `.env`; Kubernetes Secret `orbitcu-credentials` | `statusmap`, `firms` |
| `CDSAPI_KEY` | Copernicus CDS personal token | same | `era5` and the ERA5 helpers |
| Object storage credential (`RCLONE_CONFIG_ORBITCU_SAS_URL`) | one container, no delete right, with an expiry | Kubernetes Secret only | rclone push and restore |
| `GITHUB_TOKEN` | provided by GitHub per workflow run | GitHub, automatic | `docker-publish.yml` (packages: write) |
| GHCR pull token (only if the package is private) | `read:packages` | Kubernetes `docker-registry` Secret | image pulls |

Not secrets: `CDSAPI_URL`, worker counts, data paths. They go in the ConfigMap.

## 3. Runtime secrets (Kubernetes)

Create the Secret from the shell; it is never written to Git:

```bash
kubectl -n orbitcu create secret generic orbitcu-credentials \
  --from-literal=FIRMS_MAP_KEY="$FIRMS_MAP_KEY" \
  --from-literal=CDSAPI_KEY="$CDSAPI_KEY" \
  --from-literal=RCLONE_CONFIG_ORBITCU_TYPE=azureblob \
  --from-literal=RCLONE_CONFIG_ORBITCU_SAS_URL="$ORBITCU_SAS_URL"
```

To rotate a key, run the same command with `--dry-run=client -o yaml`, pipe
it to `kubectl apply -f -`, and the next Job uses the new value.

Rules:
- `infra/kubernetes/base/secret.example.yaml` (planned) holds placeholders
  only, such as `REPLACE_ME`. It is **not** listed in `kustomization.yaml`,
  so it can never be applied by mistake.
- Kubernetes Secrets are only base64-encoded. On k3s, enable
  `--secrets-encryption`. On AKS, prefer Azure Key Vault with the Secrets
  Store CSI driver and workload identity, which keeps keys out of etcd.
  This is optional.
- Secrets are injected with `envFrom`. The pipeline script never echoes the
  environment and never uses `set -x`.

## 4. Container image
- No `ARG` or `ENV` carries credentials. Keys are **never** passed as
  `--build-arg`, because build arguments are visible in `docker history`.
- `.dockerignore` excludes `.env`, `*.env`, `secrets/`, `output/`, `cache/`,
  `*.parquet`, `*.tif` and `*.nc` ([DOCKER.md §3](DOCKER.md#3-planned-dockerignore)).
- The image runs as non-root UID 10001.
- Base image pinned by digest, and dependencies pinned by `constraints.txt`.
- Optional image scan in CI (e.g. Trivy), reported but not blocking at first.

## 5. Pod hardening (planned manifests)
`runAsNonRoot`, `runAsUser: 10001`, `allowPrivilegeEscalation: false`,
`capabilities.drop: [ALL]`, `readOnlyRootFilesystem: true`,
`seccompProfile: RuntimeDefault`, `automountServiceAccountToken: false`, no
RBAC, no `hostPath`, no privileged containers. See [KUBERNETES.md §6](KUBERNETES.md#6-security-settings-summary).

## 6. GitHub Actions
- `ci.yml`: `permissions: contents: read`; no secrets; safe for fork PRs.
- `docker-publish.yml`: `contents: read`, `packages: write`; runs only on
  pushes to `developpement` or `main`, on `v*` tags and on manual dispatch;
  **never** on `pull_request` or `pull_request_target`.
- Actions pinned to major versions, or to commit SHAs with Dependabot updates.
- Branch protection on `developpement` and `main`, requiring the `ci`
  checks. This is a repository setting made by a maintainer.
- Enable GitHub secret scanning and push protection (repository Settings →
  Code security).

## 7. Object storage
- Private container, no anonymous access.
- The cluster uses a **container-scoped SAS** with read, add, create, write
  and list rights, **without delete**, and with an expiry date (e.g. 6
  months, renewal noted in [OPERATIONS.md §6](OPERATIONS.md#6-routine-checks-planned)).
  Even a leaked credential cannot erase history.
- Versioning and soft delete are on, protecting against overwrite and accidental deletion.
- The account keys are not used by the cluster.

## 8. Local secret files

`.gitignore` already contains `.env`. Phase 9 will add:

```
*.env
!.env.example
secrets/
*.sas
kubeconfig*
```

and a `.env.example` with placeholders only:

```
FIRMS_MAP_KEY=replace-with-your-firms-map-key
CDSAPI_KEY=replace-with-your-cds-token
# CDSAPI_URL=https://cds.climate.copernicus.eu/api
# RCLONE_CONFIG_ORBITCU_TYPE=azureblob
# RCLONE_CONFIG_ORBITCU_SAS_URL=replace-with-container-sas-url
```

## 9. If a key leaks
1. Revoke it at the provider: FIRMS → request a new MAP_KEY; CDS → regenerate
   the token; Azure → revoke the SAS (rotate the account key, or delete the
   stored access policy).
2. Update the developer `.env` files and the Kubernetes Secret (§3).
3. If it was committed: removing it in a new commit is **not enough**. The
   key must be treated as public and revoked. History rewriting is optional
   and must be agreed by the team, because it affects shared branches.
