# OrbitCu — Environment Setup

This guide covers three set-ups:

1. **Local Python**, which works today.
2. **Docker**, which needs the planned `Dockerfile`.
3. **Local Kubernetes with Kind**, which needs the planned `infra/`.

Sections 2 and 3 describe planned infrastructure; see
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## 1. Local Python (existing, works today)

Requirements: Python **3.10 or later** and Git. The team uses Windows 11
laptops. The commands below were also verified on Linux with Python 3.13.

```bash
git clone https://github.com/RobinDemClu/OrbitCu---SWE.git
```

```bash
cd OrbitCu---SWE
```

A virtual environment is recommended:

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

On Windows PowerShell, activate it with `.venv\Scripts\Activate.ps1`.

Install **in editable mode with all extras**. Editable mode is required
because the package finds `data/registry/` relative to the source tree. The
extras are required because the tests need scipy.

```bash
pip install -e ".[dev,era5,s5p,emissions]"
```

Run the tests (offline). On 27 November 2026 the result was
`53 passed, 6 warnings`:

```bash
pytest
```

List the commands:

```bash
orbitcu --help
```

`statusmap`, `image-scores` and `so2-copper` are dispatched separately and do
not appear in the main help. Use `orbitcu statusmap -h`,
`orbitcu image-scores -h` and `orbitcu so2-copper -h`.

### 1.1 Credentials

| Variable | Needed for | Get it |
|---|---|---|
| `FIRMS_MAP_KEY` | `orbitcu firms`, `orbitcu statusmap` (unless `--no-download`) | https://firms.modaps.eosdis.nasa.gov/api/map_key/ (free) |
| `CDSAPI_KEY` | `orbitcu era5` | Copernicus CDS account, personal access token; accept the ERA5 licence once on the CDS website |
| `CDSAPI_URL` | optional | Default `https://cds.climate.copernicus.eu/api` |

Sentinel-2, Landsat and Sentinel-5P come from Microsoft Planetary Computer
**without a key**.

Linux / macOS:

```bash
export FIRMS_MAP_KEY="your-key"
```

```bash
export CDSAPI_KEY="your-token"
```

Windows PowerShell: `$env:FIRMS_MAP_KEY = "your-key"`. To make it permanent:
`setx FIRMS_MAP_KEY "your-key"`, then open a new terminal.

Never commit keys. `.env` is already in `.gitignore`.

### 1.2 Where data goes

By default, outputs go to `output/` and the FIRMS history cache to
`cache/firms_hist/`, **relative to the directory you run the command from**.
Both are git-ignored. Always run from the repository root, or pass
`--out` / `--firms-dir` explicitly. See [STORAGE.md](STORAGE.md) for the full
layout.

## 2. Docker (planned)

Requirements: Docker Desktop (Windows/macOS) or Docker Engine (Linux).

Create a local env file for the container (git-ignored, never committed).
`.env.example` is a planned template that will hold placeholders only:

```bash
cp .env.example .env
```

Until `.env.example` exists, create `.env` by hand with two lines:
`FIRMS_MAP_KEY=...` and `CDSAPI_KEY=...`.

Build and run:

```bash
docker build -t orbitcu:dev .
```

```bash
docker run --rm --init --env-file .env -v "$PWD/work:/work" orbitcu:dev statusmap --months 3
```

Details, the Linux permission note and the verification results are in
[DOCKER.md](DOCKER.md).

## 3. Local Kubernetes with Kind (planned)

Requirements:

* Docker
* `kubectl` 1.27 or later (CronJob `timeZone` support)
* Kind (https://kind.sigs.k8s.io/)
* rclone, only to inspect the remote storage from your laptop

```bash
kind create cluster --name orbitcu
```

```bash
kubectl cluster-info --context kind-orbitcu
```

Kind's default storage class (`standard`, local-path) gives the PVC its
storage. The data lives inside the Kind node container: **deleting the Kind
cluster deletes the PVC**. That is why object storage is the durable copy.

Then follow [KUBERNETES.md](KUBERNETES.md), §5 (load the image, create the
Secrets, `kubectl apply -k`).

### 3.1 Files you create locally (all git-ignored)

| File | Content | Used by |
|---|---|---|
| `.env` | `FIRMS_MAP_KEY=...`, `CDSAPI_KEY=...` | `docker run --env-file` |
| `.env.k8s` | Same keys | `kubectl create secret generic orbitcu-credentials --from-env-file` |
| `rclone.conf` | Storage remote `orbitcu-blob` ([STORAGE.md](STORAGE.md), §5) | `kubectl create secret generic orbitcu-storage --from-file` |

The planned `.gitignore` additions cover `.env.*` (except `.env.example`),
`rclone.conf` and `*.kubeconfig` ([SECURITY.md](SECURITY.md), §4). Until they
are added, **check `git status` before every commit**.

## 4. Cloud resources (not created; need team approval)

| Resource | Purpose | Cost |
|---|---|---|
| Azure Storage account (StorageV2, LRS) and container `orbitcu` | Durable data | Azure for Students credit |
| Optional: one small Linux VM with k3s | Unattended daily runs (Kind runs only while a laptop is on) | Azure for Students credit |
| GHCR package `orbitcu` | Container image | Free for public packages |

Example commands, **for when the team has approved them** (replace the
names; the storage account name must be globally unique and lowercase):

```bash
az group create --name orbitcu-rg --location westeurope
```

```bash
az storage account create --name <uniqueaccountname> --resource-group orbitcu-rg --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false
```

```bash
az storage container create --account-name <uniqueaccountname> --name orbitcu --auth-mode login
```

```bash
az storage account blob-service-properties update --account-name <uniqueaccountname> --resource-group orbitcu-rg --enable-versioning true --enable-delete-retention true --delete-retention-days 30 --enable-container-delete-retention true --container-delete-retention-days 30
```

```bash
az lock create --name orbitcu-no-delete --resource-group orbitcu-rg --lock-type CanNotDelete
```

The SAS token for rclone is described in [SECURITY.md](SECURITY.md), §3.
