# OrbitCu — CI/CD with GitHub Actions

> **Status: planned, not implemented.** The repository has **no `.github/`
> directory**, so no workflow runs today. Tests are run by hand (`pytest`).
> This document specifies the two workflows for Phases 4 and 5.

## 1. Branch model (existing)

| Branch | Use | CI | Image publish |
|---|---|---|---|
| feature branches (e.g. `feature/kubernetes-infrastructure`) | work in progress | on PR | no |
| `developpement` | integration branch (team convention, see report) | on push | yes, `sha-…` + `developpement` |
| `main` | stable | on push | yes, `sha-…` + `main` + `latest` |
| tags `v*.*.*` | releases | on tag | yes, `vX.Y.Z`, `X.Y` |

## 2. `ci.yml` — tests and validation (planned)

Triggers: `pull_request` (all branches), and `push` to `developpement` and `main`.

| Job | Steps | Notes |
|---|---|---|
| `tests` | checkout → setup-python (matrix **3.10**, **3.12**) → `pip install -e ".[dev,era5,s5p,emissions]"` (with `-c constraints.txt` once it exists) → `pytest -q` | 3.10 is the declared minimum and was not tested in the audit. The tests need no network keys. |
| `docker-build` | checkout → setup-buildx → `docker build --target test` → run the test image → `docker build` runtime target (no push) | Checks that the image builds and the tests pass inside it |
| `k8s-validate` | checkout → `kubectl kustomize` each overlay → `kubeconform -strict` | Runs only once `infra/kubernetes/` exists |
| `secrets-hygiene` | fail if tracked files match `*.env` (except `.env.example`), or if `git ls-files` lists `*.parquet`, `*.tif` or `*.nc` | Cheap guard; GitHub secret scanning is a complement |

Permissions: `permissions: contents: read` at workflow level. No secrets are
used, so PRs from forks run safely.

Failure reporting: pytest output in the job log, with `-q -rfE` for a short
summary of failures and errors. A failed job blocks the PR, if branch
protection is enabled on `developpement` and `main` (a manual repository
setting).

### Skeleton

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [developpement, main]
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  tests:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python: ["3.10", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
          cache: pip
      - run: pip install -e ".[dev,era5,s5p,emissions]"
      - run: pytest -q -rfE
```

Third-party actions are pinned to a major version (`@v4`). For stronger
supply-chain protection they can be pinned to a full commit SHA, kept up to
date with Dependabot (`.github/dependabot.yml`, ecosystem `github-actions`).

## 3. `docker-publish.yml` — publish to GHCR (planned)

Triggers: `push` to `developpement` and `main`, `push` of tags `v*.*.*`, and
`workflow_dispatch`. It **does not run on `pull_request`**, so code from
untrusted forks never publishes an image and never sees a write token.

```yaml
permissions:
  contents: read
  packages: write        # only this workflow, only this permission
```

Steps:
1. `actions/checkout@v4`
2. `docker/setup-buildx-action@v3`
3. `docker/login-action@v3`, with registry `ghcr.io`, user `${{ github.actor }}`
   and password `${{ secrets.GITHUB_TOKEN }}`. This is the built-in token, so
   no personal access token is needed.
4. `docker/metadata-action@v5` with image `ghcr.io/robindemclu/orbitcu` and these tags:
   - `type=sha,prefix=sha-,format=short`
   - `type=ref,event=branch`
   - `type=semver,pattern={{version}}` and `type=semver,pattern={{major}}.{{minor}}`
   - `type=raw,value=latest,enable={{is_default_branch}}`
5. `docker/build-push-action@v6` with `push: true`, `platforms: linux/amd64`,
   OCI labels from the metadata step, and the GitHub Actions cache
   (`cache-from/cache-to: type=gha`).

Image name: `ghcr.io/robindemclu/orbitcu`. It is lowercase because GHCR
requires lowercase names; the GitHub owner is `RobinDemClu`. Tag convention:
see [DOCKER.md §5](DOCKER.md#5-image-naming).

### Letting Kubernetes pull the image
- **Public package (simplest):** GitHub → Packages → `orbitcu` → Package
  settings → Change visibility → Public. The image contains no secret and no
  data, so this is acceptable, and the cluster needs no pull secret.
- **Private package:** create a classic PAT with only `read:packages`, then:

```bash
kubectl -n orbitcu create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io --docker-username=<github-user> --docker-password=<PAT>
```

  Reference it from the ServiceAccount (`imagePullSecrets: [{name: ghcr-pull}]`).

## 4. Deployment (CD) — manual on purpose

There is no automatic deployment to a cluster. A runner with cluster
credentials would be a large privilege for a student project, and the
cluster is not reachable from GitHub. The deployment steps are:

1. Merge to `developpement` or `main`. The workflow publishes `sha-<short-sha>`.
2. Update `newTag` in `infra/kubernetes/overlays/vm/kustomization.yaml`
   (`images:` entry) in a PR.
3. On the cluster: `git pull`, then `kubectl apply -k infra/kubernetes/overlays/vm`.

To roll back, set the previous `sha-…` tag and apply again.

## 5. Secrets in GitHub
- CI needs **no secrets**, because the tests run offline.
- Publishing uses only `GITHUB_TOKEN`.
- `FIRMS_MAP_KEY` and `CDSAPI_KEY` are **not** stored as GitHub secrets
  unless a future workflow needs real downloads. In that case, use a
  protected GitHub Environment and never use it on `pull_request`.

## 6. Pre-existing test status
The audit found 53 test cases, all passing on Python 3.13 (27 Nov 2026). If
CI later fails on 3.10 or 3.12, compare with a local run on the same commit
**before** blaming the infrastructure, and record the result in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). Tests are never skipped
or weakened to make CI pass.
