# Production deployment — external Postgres 17 / Redis / S3

Single-host, Docker-Compose deployment of the whole Unstract platform with the
three stateful services supplied **externally**:

| Service    | Provided by            | Notes |
|------------|------------------------|-------|
| PostgreSQL | **external** Postgres 17 | must have `pgvector` installed (doc indexer stores vectors) |
| Redis      | **external** Redis       | plaintext or TLS (`rediss://`) |
| S3         | **external** object store | AWS S3 or any S3-compatible endpoint |
| Everything else | **this stack** (Docker on one host) | backend, frontend, platform-service, x2text, runner, the PG-era worker fleet, Traefik, Flipt, Qdrant, RabbitMQ |

> RabbitMQ, Flipt and Qdrant stay in the stack. RabbitMQ is still the Celery
> broker for tool-execution RPC; Flipt backs feature flags; Qdrant backs the
> document indexer's vector store. They are stateful but small and
> single-host-friendly — move them out only if you have a reason to.

This is a **separate top-level Compose file**, not an override of
`docker-compose.yaml`. The dev file hardcodes `ENVIRONMENT=development` on every
worker, points storage at MinIO, and `depends_on` the MinIO services — and a
Compose *override* can only add, never remove, those references. So production
gets its own file that simply omits them. **The dev path is untouched.**

---

## Files added

| File | Role |
|------|------|
| `docker-compose.prod.yaml` | the production stack (app + in-stack infra, no db/redis/minio) |
| `sample.prod.essentials.env` | external endpoints + secrets; also feeds Compose interpolation |
| `sample.prod.backend.env` | backend-only production hardening (optional) |
| `sample.prod.platform-service.env` | platform-service overrides (optional) |
| `sample.prod.runner.env` | runner overrides (optional) |
| `sample.prod.x2text.env` | x2text overrides (optional) |
| `sample.prod.workers.env` | worker-fleet overrides — flips `ENVIRONMENT` to `production` |

The `sample.prod.*.env` files are **deltas**: they are merged on top of the
per-service `.env` files (created from the dev samples), so they only carry what
changes for an external-infra production run.

---

## One-time prerequisites

### 1. External PostgreSQL 17

The app runs `manage.py migrate` on start and expects to create its schema and a
helper database, so the connecting role needs to **own** the target database.
Run once as a superuser:

```sql
CREATE ROLE unstract WITH LOGIN PASSWORD '<strong-password>' CREATEDB;
CREATE DATABASE unstract_db OWNER unstract;
-- pgvector is required (doc indexer vector columns):
CREATE EXTENSION IF NOT EXISTS vector;          -- in the database you just made
```

`manage.py migrate` creates the `unstract` schema itself, but the role must be
able to. If you pre-create it, do it as the `unstract` role (or grant it):

```sql
CREATE SCHEMA IF NOT EXISTS unstract AUTHORIZATION unstract;
ALTER DATABASE unstract_db OWNER TO unstract;
```

Version note: the dev image is `pgvector/pgvector:pg15`; the platform is
version-agnostic, so an external **Postgres 17 with the pgvector extension**
works. Confirm `SELECT extversion FROM pg_extension WHERE extname='vector';`
returns a version compatible with the `pgvector` Python client pinned in
`backend/uv.lock`.

Reachability: every container dials `POSTGRES_HOST:POSTGRES_PORT`. If Postgres
lives on the **same host**, either expose it on a host address the containers can
route to, or add `host.docker.internal` to the DSN and set
`POSTGRES_HOST=host.docker.internal` (the app containers already carry the
`host-gateway` mapping).

### 2. External Redis

Plaintext: set `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD`.
Managed / TLS: prefer a URL whose scheme carries TLS, and leave the discrete
`REDIS_SSL*` keys commented (URL wins over discrete vars):

```
REDIS_URL=rediss://redis.example.com:6380/0?ssl_cert_reqs=required
REDIS_PASSWORD=<password>
REDIS_USER=            # blank — managed AUTH uses the built-in `default` user
```

Named ACL users are not supported platform-wide. `REDIS_SSL_CA_CERTS` is only
needed when the server CA is not publicly trusted.

### 3. External S3

Pick a bucket and credentials. For AWS S3 leave `UNSTRACT_S3_ENDPOINT_URL`
blank; for an S3-compatible store (MinIO/CEPH/Wasabi/…) set the endpoint.
`UNSTRACT_S3_REGION` is passed to boto3 via `client_kwargs`.

---

## Configuration

### `prod.essentials.env` (required)

```bash
cd docker
cp sample.prod.essentials.env prod.essentials.env
$EDITOR prod.essentials.env     # host, PG/Redis/S3 endpoints + secrets
```

This file is used **two ways**:

1. **Compose interpolation** — `docker compose --env-file prod.essentials.env`
   reads it to expand `${UNSTRACT_HOST}`, `${UNSTRACT_S3_STORAGE_JSON}`, etc. in
   `docker-compose.prod.yaml`.
2. **Service `env_file`** — services that read it directly (RabbitMQ, and the
   app/worker services) get `POSTGRES_*`, `REDIS_*`, `RABBITMQ_*` from it.

### `UNSTRACT_S3_STORAGE_JSON` (required)

Compose interpolates the storage blobs from this **single** variable, so set it
once. It is a JSON string, so **quote it** in the env file:

```bash
UNSTRACT_S3_STORAGE_JSON='{"provider": "s3", "credentials": {"key": "AKIA...", "secret": "...", "client_kwargs": {"region_name": "eu-west-1"}}}'
```

Generate it from the discrete values (AWS S3, no endpoint):

```bash
python3 - <<'PY'
import json
creds = {"key": "AKIA...", "secret": "...", "client_kwargs": {"region_name": "eu-west-1"}}
# For S3-compatible add: creds["endpoint_url"] = "https://s3.example.com"
print('UNSTRACT_S3_STORAGE_JSON=' + "'" + json.dumps({"provider": "s3", "credentials": creds}) + "'")
PY
```

> The five storage vars (`WORKFLOW_EXECUTION_FILE_STORAGE_CREDENTIALS`,
> `API_FILE_STORAGE_CREDENTIALS`, `PERMANENT_REMOTE_STORAGE`,
> `TEMPORARY_REMOTE_STORAGE`, `HITL_FILES_FILE_STORAGE_CREDENTIALS`) all resolve
> to this one blob. The platform treats these as shared-temporary + permanent
> storage; the S3 provider is used for both here. If you keep a separate
> S3-compatible store for hot/temporary files and AWS S3 for permanent, override
> the individual vars in `prod.backend.env` / `prod.workers.env`.

### Per-service `.env` files (base config)

`docker-compose.prod.yaml` still reads `backend/.env`, `platform-service/.env`,
`runner/.env`, `x2text-service/.env`, `workers/.env`. Generate them once from the
dev samples (this also creates the `ENCRYPTION_KEY` and default-auth values):

```bash
./run-platform.sh --only-env
```

Then the `prod.*.env` files override the external endpoints + production
hardening on top. `sample.prod.*.env` are deltas, so you normally only edit
`prod.essentials.env` and (for secrets/origins) `prod.backend.env` /
`prod.workers.env`.

**Secrets to rotate before exposing the platform** (set in the prod override
files):

- `DJANGO_SECRET_KEY`, `ENCRYPTION_KEY` (must match in `backend` and
  `platform-service`), `INTERNAL_SERVICE_API_KEY` (must match in `backend` and
  `workers`).
- `SYSTEM_ADMIN_PASSWORD`, `DEFAULT_AUTH_PASSWORD`.
- `POSTGRES_PASSWORD`, `RABBITMQ_DEFAULT_PASS`, `REDIS_PASSWORD`, S3 secret.

### `UNSTRACT_HOST` (required)

The public host Traefik routes on and the origin the browser uses. Set it in
`prod.essentials.env` (and keep `WEB_APP_ORIGIN_URL` / `DJANGO_APP_BACKEND_URL`
in `prod.backend.env` equal to it), or pass it on the command line. For HTTPS,
terminate TLS at your own load balancer in front of Traefik `:80`, set
`UNSTRACT_SCHEME=https`, and set `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`
to `True` in `prod.backend.env`.

---

## Run

```bash
cd docker

# base .env files (once)
# ./run-platform.sh --only-env

# prod env files
cp sample.prod.essentials.env prod.essentials.env      # + fill in
cp sample.prod.backend.env    prod.backend.env         # optional
cp sample.prod.workers.env    prod.workers.env         # optional (ENVIRONMENT=production)
# ...other prod.*.env as needed

# make sure the tool-registry config path exists (TOOL_REGISTRY_CONFIG_SRC_PATH)
mkdir -p ../unstract/tool-registry/tool_registry_config

VERSION=latest \
  docker compose -f docker-compose.prod.yaml --env-file prod.essentials.env up -d

docker compose -f docker-compose.prod.yaml --env-file prod.essentials.env logs -f backend
```

Then browse to `http://<UNSTRACT_HOST>` (add a DNS record or `/etc/hosts` entry
pointing it at this host). The backend runs migrations on start; watch its log
for `Migration initiated` → `Running in production mode`.

Stop / update:

```bash
docker compose -f docker-compose.prod.yaml --env-file prod.essentials.env down
# pull new images, then `up -d` again
```

---

## What runs (and what doesn't)

**Started by this stack:** `reverse-proxy` (Traefik `:80`), `feature-flag`
(Flipt), `qdrant`, `rabbitmq`, `backend`, `frontend`, `platform-service`,
`x2text-service`, `runner`, `worker-pg-orchestrator-{api,general}`,
`worker-pg-fileproc`, `worker-pg-callback`, `worker-pg-scheduler`,
`worker-pg-metrics`, `worker-pg-executor`, `worker-pg-ide-callback`,
`worker-pg-notification`, `worker-pg-reaper`, `worker-log-stream-consumer`,
`worker-log-history-scheduler-v2`.

**Not started (external instead):** Postgres, Redis, MinIO/S3.

**Not started (optional / dev-only):** `celery-flower`, `unstructured-io`
(add them yourself if needed), the retired Celery workers.

---

## Verification checklist

- `docker compose ... ps` — every service `Up`; `worker-pg-reaper` is the single
  leader (run exactly one).
- `backend` log: migrations applied, Gunicorn `Running in production mode`.
- UI loads at `http://<UNSTRACT_HOST>`; login works (CORS/CSRF correct ⇒
  `WEB_APP_ORIGIN_URL` matches the host).
- Create an adapter, upload a file, run a workflow → object appears in the S3
  bucket under `unstract/execution/...`.
- Execution logs stream in the UI (⇒ Redis log transport wired;
  `LOG_TRANSPORT=redis` is set on backend, runner, and every worker).
- Scheduled pipeline fires on time (⇒ PG scheduler adopted periodics;
  `PG_SCHEDULER_ADOPT_PERIODICS=true` on backend).
- `SELECT count(*) FROM pg_queue_message;` drains after runs (⇒ PG-queue
  consumers healthy).

---

## Scaling / sizing notes

- Concurrency knobs (`PG_FILEPROC_CONCURRENCY`, `PG_EXECUTOR_CONCURRENCY`, …) and
  visibility timeouts (`*_VT_SECONDS`) are documented in `docker/sample.env` and
  the per-service compose blocks; tune them in `prod.essentials.env` for your
  host.
- `worker-pg-reaper` must stay at one replica (leader-elected). The consumers use
  `SELECT … SKIP LOCKED`, so you can raise their replica counts with
  `docker compose ... up -d --scale worker-pg-fileproc=N`.
- The PG-queue transport and the Redis log transport both hit your external
  Redis/Postgres — size those for the connection counts implied by the worker
  concurrency you pick.
