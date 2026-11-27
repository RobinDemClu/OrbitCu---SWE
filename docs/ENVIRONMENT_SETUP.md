# OrbitCu — Environment Setup

This page separates what **works today** (local Python) from what is
**planned** (Docker, Kubernetes, object storage). Planned steps depend on
files that are not in the repository yet; see
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## 1. Local Python environment (works today)

Requirements: Python ≥ 3.10, Git. Verified on 27 Nov 2026 with Python 3.13 on Linux.

```bash
git clone https://github.com/RobinDemClu/OrbitCu---SWE.git
cd OrbitCu---SWE
python -m venv .venv
```

Activate the virtual environment, on Linux / macOS:

```bash
source .venv/bin/activate
```

or on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the package and run the tests:

```bash
pip install -e ".[dev,era5,s5p,emissions]"
```

```bash
pytest -q
```

Expected: `53 passed` (no network or keys needed).

The install **must be editable** (`-e`). The code reads
`data/registry/*.csv` relative to its source folder (`src/orbitcu/sites.py`).
`.venv/` is not in `.gitignore` yet. Keep it outside the repository, or add
it to your global gitignore.

## 2. Credentials

| Variable | Needed for | How to get it |
|---|---|---|
| `FIRMS_MAP_KEY` | `orbitcu firms`, `orbitcu statusmap` | Free key: https://firms.modaps.eosdis.nasa.gov/api/map_key/ (5000 transactions / 10 min) |
| `CDSAPI_KEY` | `orbitcu era5` and the ERA5 helpers | Copernicus Climate Data Store account → personal access token; accept the ERA5 licence once on the dataset page |
| `CDSAPI_URL` | optional | defaults to `https://cds.climate.copernicus.eu/api` |

Sentinel-2, Landsat and Sentinel-5P (Planetary Computer) need **no key**.

Set the keys for the current shell session:

```bash
export FIRMS_MAP_KEY="..."
export CDSAPI_KEY="..."
```

```powershell
$env:FIRMS_MAP_KEY = "..."
$env:CDSAPI_KEY = "..."
```

Or keep them in a local `.env` file. Git already ignores `.env`. Never
commit keys, never paste them into issues or PRs, and never pass them as
Docker build arguments. See [SECURITY.md](SECURITY.md).

## 3. First commands (work today)

```bash
orbitcu --help
```

```bash
orbitcu statusmap --months 1
```

```bash
orbitcu s5p-batch --sites PL-GLOGOW --start 2026-10 --end 2026-10 --workers 2
```

- The `statusmap` command needs `FIRMS_MAP_KEY`. It writes
  `cache/firms_hist/…` and `output/status_map.html`.
- The `s5p-batch` command needs no key. It writes `output/s5p_batch/2026-10.parquet`.
- Valid site ids are the `id` column of `data/registry/smelter_sites.csv`
  (29 sites, e.g. `PL-GLOGOW`, `DE-HAMBURG`, `CL-CHUQUI`).

## 4. Docker (planned — needs the `Dockerfile`)

Install Docker Desktop (Windows/macOS) or Docker Engine (Linux), then follow
[DOCKER.md §4](DOCKER.md#4-planned-usage).

## 5. Local Kubernetes with Kind (planned — needs `infra/kubernetes/`)

Tools to install:

| Tool | Purpose | Install |
|---|---|---|
| `kind` | Kubernetes in Docker | https://kind.sigs.k8s.io/docs/user/quick-start/#installation |
| `kubectl` | cluster CLI (includes `kustomize` as `kubectl kustomize`) | https://kubernetes.io/docs/tasks/tools/ |
| `kubeconform` | offline manifest validation | https://github.com/yannh/kubeconform |
| `rclone` | sync to object storage | https://rclone.org/install/ |

Kind needs Kubernetes ≥ 1.27 for `CronJob.spec.timeZone`. Recent Kind
releases default to a newer version. Then follow
[KUBERNETES.md §4](KUBERNETES.md#4-planned-commands).

## 6. Small always-on cluster (planned)

For real daily runs a machine must be on at 04:00 UTC. Options:

1. A team machine that is always on, with Kind or k3s.
2. One small Linux VM paid from Azure for Students credit, with k3s
   (`curl -sfL https://get.k3s.io | sh -`). k3s includes the `local-path`
   storage class and metrics-server.

Creating a VM or a storage account costs money or credit. **Get the team's
approval first.**

## 7. Object storage (planned)

1. With approval, create an Azure Storage account (StorageV2, LRS) and a
   private container `orbitcu-data`. Turn on blob versioning and soft delete.
2. Create a SAS token limited to that container, with read/add/create/write/list
   rights and **no delete**, and an expiry date.
3. Configure rclone through environment variables, for example in your local
   `.env` (never committed):

```
RCLONE_CONFIG_ORBITCU_TYPE=azureblob
RCLONE_CONFIG_ORBITCU_SAS_URL=https://<account>.blob.core.windows.net/orbitcu-data?<sas>
```

4. Test the remote:

```bash
rclone ls orbitcu:orbitcu-data
```

With a container-level SAS URL, rclone still expects the container name in
the path (`orbitcu:orbitcu-data/...`), and other containers are not
reachable. Record the result in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) once the remote is set up.

Details: [STORAGE.md](STORAGE.md).
