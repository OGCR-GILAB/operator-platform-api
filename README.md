# OGCR Operator API

Django REST service that sits between the **DCR platform** (the main platform, an OBP-based API at
`dcr.ogcr.tesobe.com`) and the **operator frontend**. Registration and login happen on DCR; all
project preparation happens here until the user submits the project to DCR.

```
frontend (SPA)  --->  operator-api (this repo)  --->  DCR platform (OBP API)
                            |
                        PostgreSQL
```

## Authentication and onboarding

Accounts live on DCR. Registration is the one call that reaches DCR immediately:
`POST /api/auth/register/` creates the DCR user (`POST /obp/v6.0.0/users`), mirrors it locally and, if DCR
sent a validation e-mail, the frontend completes it with `POST /api/auth/validate-email/` `{token}`.
`POST /api/auth/password-reset/` asks DCR to e-mail a reset link; it needs a DCR service account with the
`CanCreateResetPasswordUrl` role (`DCR_SERVICE_USERNAME` / `DCR_SERVICE_PASSWORD`) and otherwise answers 503.
Users join an operator through `POST /api/operators/{id}/members/` (by username or e-mail of a user who has
logged in at least once); membership becomes a DCR `user_operator_relationship` on the first project submit.

1. The frontend sends `POST /api/auth/login/` with `username` and `password`.
2. The API forwards the credentials to DCR DirectLogin (`/my/logins/direct`) together with our
   `DCR_CONSUMER_KEY`. The consumer key never leaves the server.
3. DCR returns a token; the API fetches `/obp/v5.1.0/users/current`, creates or refreshes the local
   user (`accounts.User`, field `dcr_user_id`) and returns `{token, user}` to the frontend.
4. Every further request carries `Authorization: DirectLogin token="<token>"`.
   `DCRDirectLoginAuthentication` validates the token against DCR and caches the token -> user
   mapping for `DCR_AUTH_CACHE_SECONDS`. The token is available to views as `request.auth`, so calls
   to DCR can be made on behalf of the user.

DCR errors map to HTTP codes: bad credentials/token -> 401, DCR unreachable or not configured -> 503,
other DCR errors -> 502.

Frontend integration: [docs/frontend-guide-v4.md](docs/frontend-guide-v4.md). Partner (verification platform)
access: [docs/partner-api.md](docs/partner-api.md).

## Data model and the local-first rule

Everything is authored and stored in the local PostGIS database. The only call that reaches DCR
automatically is user registration/login. Operators, projects, plans and parcels are pushed to DCR
**only when the user explicitly submits** (`POST /api/projects/{id}/submit/`).

Local models mirror DCR dynamic entities field by field (`apps/projects/dcr_mapping.py` builds the
payloads):

| Local model | DCR entity | Notes |
|---|---|---|
| `operators.Operator` | `operator` | created locally; members via `OperatorMembership` |
| `operators.OperatorMembership` | `user_operator_relationship` | links the DCR user to the operator |
| `projects.Project` | `activity` | geometry as MultiPolygon (EPSG:4326) |
| `projects.ActivityPlan` | `activity_plan` | one per project |
| `projects.MonitoringPlan` | `monitoring_plan` | one per project; DCR keeps no link to the activity |
| `parcels.Parcel` | `parcel` | belongs to an operator; MultiPolygon (EPSG:4326), IACS/LPIS codes, cadastral reference |
| `parcels.ParcelOwnerVerification` | `parcel_owner_verification` | one per parcel; read from DCR only (written by the certifier/POV) |
| `parcels.ProjectParcel` | `parcel_activity` (legacy: `activity_parcel_verification`) | links a parcel to a project; verification state mirrored from `activity_verification` |

Every mirrored record carries `dcr_id`, `dcr_sync_status` (`local` / `synced` / `failed`),
`dcr_synced_at` and the last `dcr_response`. Submit creates entities in dependency order
(operator -> membership -> activity -> activity_plan -> for each linked parcel: parcel -> parcel/activity link
(`parcel_activity`, or the legacy `activity_parcel_verification` while DCR still declares only that) ->
monitoring_plan). Owner verifications are never pushed: the operator role bundle only reads them, the
certifier or POV component writes them. After submission,
`POST /api/projects/{id}/dcr-status/` reads `activity_verification` (one verdict per parcel and activity),
the legacy `activity_parcel_verification` and `parcel_owner_verification` from DCR and mirrors them:
all parcels `verified` -> `accepted`, any `failed` -> `rejected`;
on a DCR error the failing step is recorded and the project stays editable. Ids are assigned by DCR
on create and are never sent by the client (`manage.py dcr_probe` checks this against a real account).

## Documents

Supporting files (proof of ownership, cadastral extract, land use agreement, methodology, monitoring
report, photo, map, other) are attached to exactly one project or parcel and stored in the private
`media` volume; the API serves them only through the authenticated download endpoint. Each upload
records original name, content type, size and SHA-256. A new upload against an existing document
creates version n+1 and supersedes the previous one (history stays; `?all_versions=1` lists it).
Documents of a submitted project, a DCR-registered parcel or a forwarded document are frozen.
Limits: `DOCUMENT_MAX_SIZE_MB` (25) and `DOCUMENT_ALLOWED_EXTENSIONS`. Forwarding to the external
verification platform is prepared (`forward_status`, `forwarded_at`, `forward_reference`) but the
target is not defined yet.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health/` | liveness/readiness (checks the database) |
| GET | `/api/reference/`, `/api/reference/countries/` | vocabularies (activity/unit types, statuses, document kinds) and ISO countries; public |
| POST | `/api/auth/register/` | register on DCR and mirror locally |
| POST | `/api/auth/validate-email/` | confirm the e-mail with the DCR token |
| POST | `/api/auth/password-reset/` | DCR sends a reset link (needs the service account) |
| POST | `/api/auth/login/` | DirectLogin proxy to DCR |
| POST | `/api/auth/logout/` | drops the cached token |
| GET | `/api/auth/me/` | current user with `dcr` block (DCR account, roles, capabilities) |
| GET | `/api/auth/dcr/certification-schemes/` | certification schemes from DCR (read-only, cached) |
| CRUD | `/api/operators/` | operators the user belongs to (staff sees all) |
| GET | `/api/operators/{id}/dcr/` | live operator record from DCR incl. `ogcr_wallet_address` (404 until registered) |
| GET/POST | `/api/operators/{id}/members/` | members; POST `{username|email, relationship}` adds one |
| DELETE | `/api/operators/{id}/members/{user_id}/` | remove a member (not the last one) |
| CRUD | `/api/projects/` | projects of the current user (staff sees all) |
| GET/PUT/PATCH | `/api/projects/{id}/plan/` | activity plan of the project |
| GET/PUT/PATCH | `/api/projects/{id}/monitoring-plan/` | monitoring plan of the project |
| GET/POST | `/api/projects/{id}/parcels/` | parcels linked to the project; POST `{parcel, amount?, status_message?}` links one |
| DELETE | `/api/projects/{id}/parcels/{parcel_id}/` | unlink a parcel (while the project is editable) |
| CRUD | `/api/parcels/` | parcels of the operators the user belongs to; geometry as GeoJSON |
| GET | `/api/parcels/geojson/` | all visible parcels as a GeoJSON FeatureCollection (map layer) |
| GET/PUT/PATCH | `/api/parcels/{id}/owner-verification/` | owner verification of the parcel |
| GET/POST | `/api/documents/` | supporting documents; multipart upload with `file`, `kind`, `title`, `project` or `parcel` |
| GET | `/api/documents/{id}/download/` | the file (authenticated; files are never served statically) |
| POST | `/api/documents/{id}/versions/` | upload a new version (previous one is kept, superseded) |
| PATCH/DELETE | `/api/documents/{id}/` | edit metadata / delete (blocked once the parent is submitted or forwarded) |
| GET | `/api/projects/{id}/readiness/` | submission checklist: blocking errors and warnings |
| GET/POST | `/api/projects/{id}/dcr-status/` | verification outcome; POST refreshes it from DCR (accepted/rejected, parcel statuses) |
| POST | `/api/projects/{id}/ready/` | draft -> ready |
| POST | `/api/projects/{id}/reopen/` | ready -> draft |
| POST | `/api/projects/{id}/submit/` | push to DCR; draft/ready -> submitted (locks the project) |
| POST | `/api/partner/parcels/search/` | partner API (`Authorization: Api-Key`): parcels intersecting a geometry with projects, owner verification and documents |
| GET | `/api/partner/documents/{id}/download/` | partner download of a document |
| GET | `/api/partner/ping/` | partner key check |
| GET | `/api/docs/` | Swagger UI (`/api/schema/` is the OpenAPI JSON) |
| - | `/admin/` | Django admin |

Project lifecycle: `draft -> ready -> submitted -> accepted | rejected`.
After `submitted` the project is read-only (PATCH/DELETE return 409). Submit returns 400 with a
`problems` list when required fields are missing and 502 with the failing `step` on DCR errors.

## Running

Prerequisites: Docker + Docker Compose v2. The image installs GDAL (`gdal-bin`) for GeoDjango.

```sh
cp .env.example .env        # fill in DCR_CONSUMER_KEY / SECRET, change passwords
docker compose up -d --build
curl http://localhost:8000/api/health/
```

The container entrypoint waits for the database, runs `migrate` and `collectstatic`, then gunicorn
(`RUN_MIGRATIONS=0` / `COLLECT_STATIC=0` disable those steps).

### Development from PyCharm (recommended)

The application runs from `.venv`; PostgreSQL is the Docker `db` service published on `127.0.0.1:5432`
(same PostGIS image as on the server). `.env` is loaded automatically (python-dotenv), `DB_HOST=127.0.0.1`
(not `localhost`: on Windows it resolves to IPv6 `::1` where Docker does not listen and the connection hangs).
GeoDjango needs GDAL/GEOS: on Windows point `GDAL_LIBRARY_PATH` and `GEOS_LIBRARY_PATH` in `.env` at the
QGIS or OSGeo4W DLLs (see `.env.example`); on Linux/Docker leave them empty.

```sh
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py runserver
```

PyCharm run configurations are provided: **runserver**, **tests**, **migrate**, **makemigrations**
(`.idea/runConfigurations/`). The interpreter is `.venv` in the project root.

### Development in Docker (runserver + bind mount)

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api python manage.py makemigrations
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api python manage.py test
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api python manage.py createsuperuser
```

Lint/format (locally, `pip install -r requirements-dev.txt`): `ruff check .` and `ruff format .`.

### Container image

Release images are published on Docker Hub as `team4gilab/operator-platform-api` (tags `vX.Y.Z`,
`X.Y` and `latest`). Configuration, ports, health checks, volumes and a Kubernetes example are in
[docs/deployment.md](docs/deployment.md).

## Security measures

- Authentication: DCR DirectLogin tokens validated against DCR (cached, hashed cache keys), partner
  API keys stored as SHA-256; the Django admin uses local passwords with django-axes lockout
  (5 failures per username+IP, 1 hour; `manage.py axes_reset` to unlock).
- Authorisation: every queryset is scoped to the requesting user (owner or operator membership);
  related-object fields are validated against membership; staff sees everything; partner keys see
  parcels of all operators by design.
- Rate limits (real client IP behind the proxy, shared database cache): anonymous 120/min, users
  3000/hour, login 10/min, registration 5/min, password reset 5/min.
- Input limits: request bodies above 30 MB are rejected with 413 by the proxy (Content-Length check) and
  again by the API before the body is read; geometries are capped at `MAX_GEOMETRY_VERTICES` (50 000) and must be valid; uploads are
  checked for size, non-empty content and an extension allowlist; files are stored under random names
  and served only through authenticated downloads as attachments.
- Transport: HTTPS only (the proxy redirects), HSTS, `Secure`/`HttpOnly`/`SameSite=Lax` cookies,
  `X-Frame-Options: DENY`, `nosniff`, same-origin referrer policy, explicit CORS origins.
- Secrets never live in the repo (environment variables or a secret store); DEBUG is off in
  production; dependencies are pinned and checked with `pip-audit`.
- The API is meant to run behind a TLS-terminating reverse proxy or ingress; the container itself
  serves plain HTTP on port 8000.

## Layout

```
config/            settings, urls, wsgi/asgi
apps/core/         health endpoint, shared HTTP exceptions (409/502/503)
apps/accounts/     custom User (mirror of the DCR user), sync service
apps/dcr/          DCRClient (OBP), DirectLogin authentication, login/logout/me, error mapping
apps/operators/    Operator + membership (DCR operator / user_operator_relationship)
apps/projects/     Project, ActivityPlan, MonitoringPlan, DCR mapping, submit service, ViewSet
apps/parcels/      Parcel (PostGIS), owner verification, project-parcel links
apps/documents/    versioned supporting documents with private download
apps/partners/     API keys and the partner endpoints for the verification platform
docker/            entrypoint, wait_for_db, gunicorn config
```

## Next steps

- Forward documents to the external verification platform once its API is known.
- Field sheet 2026-09-24: vocabularies (`co_benefits`, `technologies_practices_processes`, SDGs),
  `activity_media`, `supporting_document` entities and `country` references once DCR declares them.
