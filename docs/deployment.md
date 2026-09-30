# Deployment

The API ships as one container image. It needs a PostgreSQL database with PostGIS, a writable volume
for uploaded documents, and a TLS-terminating reverse proxy or ingress in front of it.

- Image: `team4gilab/operator-platform-api` on Docker Hub (tags `vX.Y.Z`, `X.Y`, `latest`)
- Frontend image: `team4gilab/operator-platform` (static site served by nginx on port 80; the API
  address is built into the image and points to `https://api-operator.gilab.rs/api/`)
- Architecture: `linux/amd64`

## Container

| Item | Value |
|---|---|
| Port | `8000` (HTTP, gunicorn) |
| User | non-root, uid/gid `1000` |
| Liveness / readiness | `GET /api/health/` returns `200 {"status":"ok","database":"ok","version":"…"}`, `503` when the database is unreachable |
| Writable paths | `/app/media` (uploaded documents, must be persistent), `/app/staticfiles` (collected at start, may be ephemeral) |
| Start-up | waits for the database, runs migrations and `createcachetable`, collects static files, then starts gunicorn |

Start-up steps can be switched off with `RUN_MIGRATIONS=0` and `COLLECT_STATIC=0`. With more than one
replica, run migrations once as a separate Job (same image, command
`python manage.py migrate --noinput && python manage.py createcachetable`) and set
`RUN_MIGRATIONS=0` on the Deployment.

Uploaded documents live on the filesystem under `/app/media`. With more than one replica the volume
must be shared (ReadWriteMany), or keep a single replica.

## Database

PostgreSQL 16 with the PostGIS 3 extension (the image `postgis/postgis:16-3.5` works). The database
user needs the right to create extensions on first migration, or PostGIS must be enabled beforehand
(`CREATE EXTENSION postgis;`).

## Configuration

All configuration is through environment variables.

### Required

| Variable | Meaning |
|---|---|
| `DJANGO_SECRET_KEY` | long random string, keep secret |
| `DJANGO_ALLOWED_HOSTS` | comma-separated host names the API answers to, e.g. `api.example.org` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | comma-separated HTTPS origins, e.g. `https://api.example.org,https://app.example.org` |
| `CORS_ALLOWED_ORIGINS` | comma-separated frontend origins allowed to call the API |
| `DJANGO_BEHIND_PROXY` | `1` when TLS is terminated in front of the container (enables secure cookies, HSTS, forwarded headers) |
| `DB_HOST`, `DB_PORT` | database host and port |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | database name and credentials |
| `DCR_BASE_URL` | DCR platform API host, e.g. `https://dcr.ogcr.tesobe.com` |
| `DCR_CONSUMER_KEY`, `DCR_CONSUMER_SECRET` | consumer credentials issued by DCR for this application |

### Optional

| Variable | Default | Meaning |
|---|---|---|
| `DCR_API_VERSION` | `v6.0.0` | DCR API version |
| `DCR_SERVICE_USERNAME`, `DCR_SERVICE_PASSWORD` | empty | DCR service account; enables password reset e-mails |
| `DCR_TIMEOUT` | `15` | seconds per DCR call |
| `DCR_AUTH_CACHE_SECONDS` | `300` | how long a validated DCR token is cached |
| `DCR_REFERENCE_CACHE_SECONDS` | `600` | cache for reference data read from DCR |
| `DCR_REFERENCE_SAMPLE_FALLBACK` | `1` | serve sample certification schemes while DCR has none |
| `DJANGO_DEBUG` | `0` | never enable in production |
| `DJANGO_HSTS_SECONDS` | `31536000` | HSTS max-age when behind a proxy |
| `LOG_LEVEL` | `INFO` | logs go to stdout |
| `GUNICORN_WORKERS` | `3` | worker processes |
| `GUNICORN_TIMEOUT` | `60` | request timeout in seconds |
| `DB_CONN_MAX_AGE` | `60` | persistent connection lifetime |
| `DB_CONNECT_TIMEOUT` | `10` | seconds |
| `DB_WAIT_SECONDS` | `60` | how long start-up waits for the database |
| `DOCUMENT_MAX_SIZE_MB` | `25` | upload limit per document |
| `DOCUMENT_ALLOWED_EXTENSIONS` | pdf, images, office, csv, txt, zip, geojson, json, kml, gpkg | comma-separated allowlist |
| `MAX_GEOMETRY_VERTICES` | `50000` | limit on submitted geometries |
| `AXES_FAILURE_LIMIT` | `5` | admin login failures before lockout |
| `RUN_MIGRATIONS`, `COLLECT_STATIC` | `1` | start-up steps, see above |

Configure the reverse proxy or ingress to accept request bodies up to 30 MB.

## Kubernetes example

A minimal single-replica setup. Secrets and host names are placeholders.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: operator-api
type: Opaque
stringData:
  DJANGO_SECRET_KEY: "change-me"
  POSTGRES_PASSWORD: "change-me"
  DCR_CONSUMER_KEY: "change-me"
  DCR_CONSUMER_SECRET: "change-me"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: operator-api
data:
  DJANGO_ALLOWED_HOSTS: "api.example.org"
  DJANGO_CSRF_TRUSTED_ORIGINS: "https://api.example.org,https://app.example.org"
  CORS_ALLOWED_ORIGINS: "https://app.example.org"
  DJANGO_BEHIND_PROXY: "1"
  DB_HOST: "postgis"
  DB_PORT: "5432"
  POSTGRES_DB: "operator"
  POSTGRES_USER: "operator"
  DCR_BASE_URL: "https://dcr.ogcr.tesobe.com"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: operator-api-media
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: operator-api
spec:
  replicas: 1
  selector:
    matchLabels: {app: operator-api}
  template:
    metadata:
      labels: {app: operator-api}
    spec:
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      containers:
        - name: api
          image: team4gilab/operator-platform-api:v0.1.0
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef: {name: operator-api}
            - secretRef: {name: operator-api}
          volumeMounts:
            - {name: media, mountPath: /app/media}
          readinessProbe:
            httpGet: {path: /api/health/, port: 8000}
            initialDelaySeconds: 20
            periodSeconds: 10
          livenessProbe:
            httpGet: {path: /api/health/, port: 8000}
            initialDelaySeconds: 60
            periodSeconds: 30
          resources:
            requests: {cpu: 250m, memory: 384Mi}
            limits: {memory: 1Gi}
      volumes:
        - name: media
          persistentVolumeClaim: {claimName: operator-api-media}
---
apiVersion: v1
kind: Service
metadata:
  name: operator-api
spec:
  selector: {app: operator-api}
  ports:
    - port: 80
      targetPort: 8000
```

## First steps after deployment

- Create an admin account: `python manage.py createsuperuser` in the API container.
- Partner API keys: `python manage.py create_partner_key --name <partner>` (the key is printed once).
- Interactive API documentation: `/api/docs/`. Django admin: `/admin/`.
