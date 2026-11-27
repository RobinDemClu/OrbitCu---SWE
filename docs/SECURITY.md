# OrbitCu — Security and Secrets

> **Status:** today, keys are environment variables on team laptops, and
> `.env` is git-ignored. Everything about containers, Kubernetes Secrets,
> storage credentials and CI permissions below is **planned**.

## 1. Credential inventory (verified in the code)

| Secret | Read by | Scope | Where it lives (planned) |
|---|---|---|---|
| `FIRMS_MAP_KEY` | `src/orbitcu/ingest/firms.py` (`_key()`) | NASA FIRMS API, 5000 transactions / 10 min | Kubernetes Secret `orbitcu-credentials` |
| `CDSAPI_KEY` | `src/orbitcu/ingest/era5.py` | Copernicus CDS account | Kubernetes Secret `orbitcu-credentials` |
| `CDSAPI_URL` | `src/orbitcu/ingest/era5.py` | Not secret (endpoint) | Optional, ConfigMap |
| `PC_SDK_SUBSCRIPTION_KEY` | `planetary-computer` library | Optional; OrbitCu works anonymously | Not used |
| Storage credential (Azure SAS URL, or S3 key pair) | rclone (planned) | Blob container `orbitcu` | Kubernetes Secret `orbitcu-storage` (`rclone.conf`) |
| `GITHUB_TOKEN` | GitHub Actions (planned) | Built-in, per run | Provided by GitHub; nothing to store |
| GHCR pull token | Kubernetes (only if the package is private) | `read:packages` | Secret `ghcr-pull` |

**No credential is needed to run the tests or to build the image.**

## 2. Rules

1. Never commit credentials. Never paste them into issues, PRs, chat or logs.
2. Never pass credentials as Docker `--build-arg`, and never write them with
   `ENV` in a Dockerfile. Build arguments and `ENV` lines are stored in the
   image history.
3. Give them only at run time: `docker run --env-file .env`, or Kubernetes
   `secretRef`.
4. GitHub Secrets are used **only** by GitHub Actions, and the planned
   workflows need none besides `GITHUB_TOKEN`. Do not upload data-provider
   keys to GitHub.
5. Least privilege everywhere: a SAS without the delete permission, CI jobs
   with `contents: read`, `packages: write` only on the publish job, no
   Kubernetes API token in the pods.
6. Rotate a key immediately if it is exposed: regenerate it at the provider,
   then recreate the Secret.

## 3. Creating the Kubernetes Secrets (planned)

Create them from local files, so that values never appear in shell history
or in YAML in Git.

`.env.k8s` (git-ignored) contains:

```
FIRMS_MAP_KEY=<your key>
CDSAPI_KEY=<your token>
```

Create the Secret from it:

```bash
kubectl -n orbitcu create secret generic orbitcu-credentials --from-env-file=.env.k8s
```

`rclone.conf` (git-ignored) holds the content shown in
[STORAGE.md](STORAGE.md), §5. Create the Secret from it:

```bash
kubectl -n orbitcu create secret generic orbitcu-storage --from-file=rclone.conf=./rclone.conf
```

Check which keys are present (this prints the names, not the values):

```bash
kubectl -n orbitcu describe secret orbitcu-credentials
```

To update a Secret, delete it and create it again (or use
`kubectl create ... --dry-run=client -o yaml | kubectl apply -f -`).

**Azure SAS for rclone (planned).** Make it a container-level SAS for
`orbitcu` with permissions **read, add, create, write, list** (no
**delete**), HTTPS only, and an expiry date (for example the end of the
semester). Create it in the portal (Storage account → Containers → orbitcu →
Shared access tokens) or with `az storage container generate-sas`. Record
the expiry date in the team's decision log, because the jobs stop working
when it expires.

Note: Kubernetes Secrets are only base64-encoded, and on Kind they are not
encrypted at rest. That is acceptable for a laptop cluster. On AKS, prefer
Azure Key Vault with the Secrets Store CSI driver and workload identity,
which also removes the SAS altogether.

## 4. Planned `.gitignore` additions

The current `.gitignore` already has `.env`, `/output/` and `/cache/`. Add:

```
# local secrets (keep .env.example)
.env.*
!.env.example
rclone.conf
*.kubeconfig
kubeconfig
secret.yaml
# pipeline logs and partial writes
/logs/
*.tmp
# local Docker volume
/work/
```

Planned `.env.example` (placeholders only):

```
# Copy to .env (never commit .env)
FIRMS_MAP_KEY=replace-with-your-firms-map-key
CDSAPI_KEY=replace-with-your-cds-token
# CDSAPI_URL=https://cds.climate.copernicus.eu/api
```

## 5. Code-level observations (not changed: outside the infrastructure scope)

* No code path prints a key. However, `firms.transactions_used()` and the
  FIRMS request URLs contain `MAP_KEY` **in the URL path or query**. If a
  future change logs request URLs or full exception messages from
  `requests`, the key would appear in the logs. Keep error messages to the
  status code and response text. Logs are uploaded to Blob, which widens the
  exposure.
* `era5.py` uses `os.environ["CDSAPI_KEY"]`. A missing key raises `KeyError`
  with the variable **name** only, which is safe.

## 6. Container and cluster hardening (planned, see the manifests)

* Image: non-root UID 10001, no compilers, no secrets, tini as PID 1.
* Pods: `runAsNonRoot`, `allowPrivilegeEscalation: false`, all capabilities
  dropped, read-only root filesystem, `seccompProfile: RuntimeDefault`,
  `automountServiceAccountToken: false`.
* Third-party images pinned to versions (`rclone/rclone:1.68.2`); pin them by
  digest for stronger guarantees.
* CI: no `pull_request_target`, no secrets in PR runs, actions pinned (SHA
  pinning recommended), image provenance attestations (`provenance: true`).

## 7. Checks done on 27 November 2026

| Check | Result |
|---|---|
| Git history (48 commits, all branches) searched for key and token patterns | Nothing found |
| `.env`, `rclone.conf`, kubeconfig and key files ever committed | None |
| Satellite imagery or caches tracked (`*.tif`, `*.nc`, `*.parquet`) | None. Only small CSV/JSON tables in `data/` |
| Secrets in the test image (`env`) | None (only the base image's public `GPG_KEY` for Python release signatures) |

Recommended: enable GitHub **secret scanning** and **push protection**
(repository Settings → Code security). They are free for public repositories.
