# OrbitCu — Docker

> **Status: planned. There is no `Dockerfile` in the repository yet.**
> The Dockerfile below is a proposal. It was **build-tested on 27 November
> 2026** in a sandbox (Docker 29.9, `linux/amd64`), with two
> sandbox-specific deviations that are listed in §6. It becomes part of the
> project only when it is committed as `Dockerfile` at the repository root
> (see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)).

## 1. Design choices

| Choice | Reason |
|---|---|
| Base `python:3.12-slim-bookworm` | Within the project's `requires-python = ">=3.10"`. Binary wheels exist for every dependency (rasterio, h5py, netCDF4, scipy, pyproj), so no compiler is needed. Pin the digest once the image is adopted. |
| `apt-get install libexpat1 tini` | **libexpat1:** the rasterio wheel links to `libexpat.so.1`, which the slim image does not ship. Without it, `orbitcu image-scores`, `so2-copper`, `optical` and the rasterio import all fail with `ImportError: libexpat.so.1` (seen during testing). **tini:** with no init process, Python as PID 1 ignores SIGTERM. The test showed `docker stop` waiting the full grace period and then SIGKILL (exit 137); with an init the stop took 1 s (exit 143). |
| Editable install (`pip install -e`) in `/app` | Required. `sites.REPO_ROOT = Path(__file__).parents[2]` must point at a checkout that contains `data/registry/` and `data/reference/`. A plain `pip install .` would break every command (see the audit, §3). |
| Extras `era5,s5p,emissions` (not `dev`) | Every CLI command works at runtime. pytest stays out of the runtime image; CI runs the tests outside the container. |
| Non-root user `orbitcu` (UID/GID 10001) | Least privilege. It matches `runAsUser: 10001` in the Kubernetes manifests. |
| `WORKDIR /work` | The CLI writes to relative paths (`output/`, `cache/`). Mounting a volume at `/work` makes every default path persistent, with no code change. |
| `ENTRYPOINT ["/usr/bin/tini","--","orbitcu"]` | `docker run IMAGE statusmap ...` behaves exactly like the local `orbitcu statusmap ...`. |
| No `ARG`/`ENV` for keys | Secrets are given only at run time (`--env-file`, Kubernetes Secret). They never appear in layers or build arguments. |

## 2. Proposed `Dockerfile`

```dockerfile
# OrbitCu runtime image. Build: docker build -t orbitcu:dev .
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libexpat1: needed by the rasterio wheel; tini: forwards SIGTERM to the CLI
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 orbitcu \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin orbitcu

# Source-checkout layout is required: sites.REPO_ROOT = Path(__file__).parents[2]
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY data/registry/ data/registry/
COPY data/reference/ data/reference/
RUN pip install -e ".[era5,s5p,emissions]"
COPY infra/scripts/ infra/scripts/

# Working directory for output/ and cache/ (mount a volume here)
RUN mkdir -p /work && chown orbitcu:orbitcu /work
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/bin/tini", "--", "orbitcu"]
CMD ["--help"]
```

The `COPY infra/scripts/` line is needed only once `infra/scripts/daily.sh`
exists (see [OPERATIONS.md](OPERATIONS.md)). Leave it out until then, or the
build fails.

## 3. Proposed `.dockerignore`

```
.git
.github
**/__pycache__
*.egg-info
.pytest_cache
output/
cache/
logs/
.env
.env.*
*.tif
*.nc
*.parquet
docs/
scripts/
tests/
data/trading/
infra/kubernetes/
```

Downloaded imagery (`*.tif`, `*.nc`, Parquet caches), local secrets and
documentation stay out of the build context, so they cannot end up in the
image. `data/registry/` and `data/reference/` are **kept**: they are small
committed CSVs that the code needs.

## 4. Build and run

Build from the repository root:

```bash
docker build -t orbitcu:dev .
```

Check the CLI:

```bash
docker run --rm orbitcu:dev --help
```

```bash
docker run --rm orbitcu:dev s5p-batch --help
```

Run a real command with a persistent working directory and keys from a local
env file. `.env` is git-ignored; see [ENVIRONMENT_SETUP.md](ENVIRONMENT_SETUP.md).

```bash
mkdir -p ./work
```

```bash
docker run --rm --init --env-file .env -v "$PWD/work:/work" orbitcu:dev firms --sites PL-GLOGOW --start 2026-10-01 --end 2026-10-07
```

Rebuild the status map from the cache only (no network, no key):

```bash
docker run --rm --init -v "$PWD/work:/work" orbitcu:dev statusmap --no-download
```

Linux note: the container runs as UID 10001, so `./work` must be writable by
that UID. Either run `sudo chown 10001:10001 work`, or add
`--user "$(id -u):$(id -g)"` to `docker run`. Docker Desktop on Windows and
macOS handles bind-mount permissions automatically.

**Do not** use `statusmap --open` in a container: it tries to start a web
browser. Open `work/output/status_map.html` on the host instead.

## 5. Verification results (27 November 2026, sandbox)

| Check | Result |
|---|---|
| Image builds | OK, about 1.19 GB (the pip layer is about 770 MB, mostly GDAL/HDF5/netCDF inside the wheels) |
| `--help` of `firms`, `optical`, `s5p`, `s5p-batch`, `era5`, `image-scores`, `so2-copper`, `statusmap` | All OK (after libexpat was added) |
| Imports of rasterio 1.5.2, h5py, netCDF4, scipy, cdsapi, xarray, h5netcdf, fsspec, pystac-client, planetary-computer | OK |
| Runs as non-root | `uid=10001(orbitcu)` |
| Registry found (editable install) | `load_sites()` returns 29 sites |
| `statusmap --no-download` with a mounted `/work` | Writes `/work/output/status_map.html` |
| Read-only root filesystem (`--read-only --tmpfs /tmp -e HOME=/tmp`) | `statusmap --no-download` and all module imports OK |
| Missing key (`firms` without `FIRMS_MAP_KEY`) | `RuntimeError: FIRMS_MAP_KEY is not set`, exit code 1 |
| SIGTERM | Without an init: ignored, killed after the grace period (137). With an init: clean stop (143). |
| No secrets in the image | Only the base image's `GPG_KEY` (Python release signing key, public) matches `*KEY*` |
| No `.tif` or `.env` in the image | Confirmed with `find` |
| Network commands against real APIs | **Not tested** (no data-provider keys in the sandbox) |
| pytest inside the image | Not applicable (tests are excluded; CI runs them, see [CI_CD.md](CI_CD.md)) |

## 6. Deviations used for the sandbox test

The sandbox has limited network access, so the test build differed from the
Dockerfile above in two ways:

1. Docker Hub returned HTTP 429. The base image was pulled through
   `mirror.gcr.io/library/python:3.12-slim-bookworm`, which serves the same
   official image.
2. `deb.debian.org` is blocked by the sandbox proxy, so **the `apt-get` line
   could not be run**. Instead, `libexpat.so.1` was copied from the official
   `python:3.12-bookworm` image to show that it is the only missing library,
   and signal handling was tested with `docker run --init` (Docker's bundled
   tini).

**What still has to be checked on a normal machine:** that the `apt-get
install libexpat1 tini` line builds and that `/usr/bin/tini` exists. CI will
do this on every pull request once `ci.yml` is in place.

## 7. Size and reproducibility (future improvements)

* Pin the base image by digest (`python:3.12-slim-bookworm@sha256:...`).
* Generate a constraints file in CI (`pip freeze > constraints.txt`) and
  install with `-c constraints.txt`. Today `pyproject.toml` pins nothing.
* A multi-stage build gains little here, because the size comes from the
  binary wheels rather than from build tools.
