# OrbitCu — CI/CD with GitHub Actions

> **Status: planned. The repository has no `.github/` directory, so no
> workflow runs today.** The report says "tests: pytest on GitHub". That is
> a goal and is **not implemented**. The two workflows below are proposals
> for `.github/workflows/`. Their YAML was parsed locally, but they have
> **never run on GitHub**.

## 1. Pipeline overview

```
 PR → developpement / main      push → developpement      push → main / tag v*.*.*
           │                            │                           │
           ▼                            ▼                           ▼
        ci.yml                       ci.yml                 ci.yml  +  docker-publish.yml
  ┌──────────────────────┐                                      ┌────────────────────────┐
  │ tests  (3.10, 3.12)  │                                      │ tests (3.12)           │
  │ docker-build (no push)│                                     │ build + push to GHCR   │
  │ k8s-manifests        │                                      │ ghcr.io/robindemclu/   │
  └──────────────────────┘                                      │   orbitcu:<sha>, ...   │
                                                                └────────────────────────┘
```

* **Pull requests never publish images and never receive secrets.**
  Workflows triggered by `pull_request` from forks get a read-only
  `GITHUB_TOKEN` and no repository secrets. No workflow uses
  `pull_request_target`.
* The tests need **no data-provider keys**: the 53 tests are offline. So
  no GitHub Secret has to be created for CI.

## 2. `ci.yml` (proposed)

```yaml
name: CI

on:
  pull_request:
    branches: [developpement, main]
  push:
    branches: [developpement, main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  tests:
    name: pytest (Python ${{ matrix.python-version }})
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
          cache-dependency-path: pyproject.toml
      - name: Install (all extras, as in the README)
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev,era5,s5p,emissions]"
      - name: Record resolved versions
        run: pip freeze > resolved-requirements.txt
      - name: Run the test suite (offline)
        run: pytest -ra
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: resolved-requirements-py${{ matrix.python-version }}
          path: resolved-requirements.txt

  docker-build:
    name: Docker build (no push)
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          load: true
          tags: orbitcu:ci
      - name: Smoke test the CLI in the image
        run: |
          docker run --rm orbitcu:ci --help
          for c in firms optical s5p s5p-batch era5 image-scores so2-copper statusmap; do
            docker run --rm orbitcu:ci "$c" --help > /dev/null
          done
          docker run --rm --entrypoint python orbitcu:ci -c "from orbitcu.sites import load_sites; assert len(load_sites()) == 29"

  k8s-manifests:
    name: Kubernetes manifests
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      KUBECONFORM_VERSION: v0.6.7
    steps:
      - uses: actions/checkout@v4
      - name: Install kubeconform
        run: |
          curl -sSLo kc.tgz "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz"
          tar xzf kc.tgz kubeconform && sudo mv kubeconform /usr/local/bin/
      - name: Render with kustomize and validate
        run: |
          kubectl kustomize infra/kubernetes > rendered.yaml
          kubeconform -strict -summary -kubernetes-version 1.31.0 rendered.yaml
          kubeconform -strict -summary -kubernetes-version 1.31.0 infra/kubernetes/backfill-job.yaml infra/kubernetes/secret.example.yaml
      - name: Pipeline script syntax
        run: bash -n infra/scripts/daily.sh
```

Notes:

* **All extras are installed** because `tests/test_emissions.py` needs scipy.
  With `.[dev]` alone, collection fails (audit §6).
* **Python 3.10** is the declared minimum but has never been tested. If it
  fails, that is a pre-existing compatibility problem in the application,
  not something caused by CI. Report it to the team; do not drop the
  version silently.
* `pytest -ra` lists every failure and skip at the end of the log, so
  failures are reported clearly. A failing test fails the job and blocks the
  PR once branch protection requires the check (§5).
* The `k8s-manifests` and `docker-build` jobs need `infra/` and a
  `Dockerfile`. Add them to `ci.yml` in the same PR that adds those files.
  The `tests` job can be merged first, on its own.
* `kubectl` is preinstalled on GitHub's `ubuntu-latest` runners. For a
  stricter supply chain, check the kubeconform tarball against the release's
  `CHECKSUMS` file.

## 3. `docker-publish.yml` (proposed)

```yaml
name: Publish image

on:
  push:
    branches: [main]
    tags: ["v*.*.*"]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  tests:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev,era5,s5p,emissions]"
      - run: pytest -ra

  publish:
    needs: tests
    # manual runs only from main or a release tag, never from other branches
    if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      packages: write
    env:
      IMAGE: ghcr.io/robindemclu/orbitcu
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.IMAGE }}
          tags: |
            type=sha,format=long,prefix=
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=main,enable=${{ github.ref == 'refs/heads/main' }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          provenance: true
```

### Image naming convention

| Tag | When | Mutable? | Use |
|---|---|---|---|
| `ghcr.io/robindemclu/orbitcu:<40-char commit SHA>` | Every publish | No | **Deployments.** Put this tag in `kustomization.yaml`. |
| `ghcr.io/robindemclu/orbitcu:1.2.3`, `:1.2` | Git tag `v1.2.3` | `1.2` moves | Releases |
| `ghcr.io/robindemclu/orbitcu:main` | Push to `main` | Yes | Manual testing only, never in manifests |

No `latest` tag is published, on purpose. GHCR image names must be
lowercase, so the owner `RobinDemClu` is written `robindemclu`.

### GHCR permissions and visibility

* The workflow authenticates with the built-in `GITHUB_TOKEN` and only the
  publish job gets `packages: write`. No personal token is stored.
* The first publish creates the package as **private**, linked to the
  repository. To let Kubernetes pull without credentials, a repository admin
  sets the package visibility to *public*
  (GitHub → Packages → orbitcu → Package settings). The image holds only
  open-source code and public registry CSVs. Otherwise, use a pull secret
  ([KUBERNETES.md](KUBERNETES.md), §5.4).

## 4. Action versions

The examples use the current major versions (`actions/checkout@v4`,
`actions/setup-python@v5`, `actions/upload-artifact@v4`,
`docker/setup-buildx-action@v3`, `docker/login-action@v3`,
`docker/metadata-action@v5`, `docker/build-push-action@v6`). Check for newer
majors when the workflows are added. For the strongest pinning, replace each
`@vN` with the full commit SHA of that release, and let Dependabot
(`.github/dependabot.yml`, ecosystem `github-actions`) propose updates.

## 5. Recommended repository settings (manual, by a repo admin)

* Branch protection on `main` (and optionally `developpement`): require the
  `tests` checks to pass before merging, and require a PR review.
* Actions → General → Workflow permissions: **Read repository contents**
  (the workflows ask for `packages: write` explicitly where needed).
* Do not add data-provider keys as GitHub Secrets. CI does not need them.

## 6. Continuous deployment

There is **no automatic deployment to a cluster**, on purpose. The cluster is
a laptop (Kind) or a student VM that GitHub cannot reach safely. Deployment
is a manual, reviewed step:

```bash
cd infra/kubernetes
```

```bash
kustomize edit set image ghcr.io/robindemclu/orbitcu=ghcr.io/robindemclu/orbitcu:<commit-sha>
```

```bash
kubectl apply -k .
```

Commit the changed `kustomization.yaml`, so that Git always records which
image runs.
