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
`POST /api/auth/register/` creates the DCR user (`POST /obp/v7.0.0/users`, optional `mobile_phone_number`),
mirrors it locally and, if DCR sent a validation e-mail, the frontend completes it with
`POST /api/auth/validate-email/` `{token}`; `POST /api/auth/resend-validation-email/` asks DCR for a new one.
Passwords are checked against DCR's own policy (`GET /api/auth/password-policy/`) before the DCR call.
`POST /api/auth/password-reset/` asks DCR to e-mail a reset link; it needs a DCR service account with the
`CanCreateResetPasswordUrl` role (`DCR_SERVICE_USERNAME` / `DCR_SERVICE_PASSWORD`) and otherwise answers 503.
The account is found on DCR by e-mail (`CanGetAnyUser`); without that role only users who registered or signed
in here can be reset.
Users join an operator through `POST /api/operators/{id}/members/` (by username or e-mail of a user who has
logged in at least once); membership becomes a DCR `user_operator_relationship` on the first project submit.

1. The frontend sends `POST /api/auth/login/` with `username` and `password`.
2. The API forwards the credentials to DCR DirectLogin (`/my/logins/direct`) together with our
   `DCR_CONSUMER_KEY`. The consumer key never leaves the server.
3. DCR returns a token; the API fetches `/obp/v7.0.0/users/current`, creates or refreshes the local
   user (`accounts.User`, field `dcr_user_id`) and returns `{token, user}` to the frontend.
4. Every further request carries `Authorization: DirectLogin token="<token>"`.
   `DCRDirectLoginAuthentication` validates the token against DCR and caches the token -> user
   mapping for `DCR_AUTH_CACHE_SECONDS`. The token is available to views as `request.auth`, so calls
   to DCR can be made on behalf of the user.

DCR errors map to HTTP codes: bad credentials/token -> 401, DCR unreachable or not configured -> 503,
other DCR errors -> 502.

## DCR API version and entity model

The API calls DCR **v7.0.0** (`DCR_API_VERSION`) and the **bank-level** dynamic entity model: OGCR entities
live under `/obp/dynamic-entity/banks/<DCR_BANK_ID>/<entity>` (default bank `ogcr`) and the roles are
`Can<Action>DynamicEntityRecord_<entity>` granted at that bank. An empty `DCR_BANK_ID` selects the older
system-level paths (`/obp/dynamic-entity/<entity>`).

- **Schema-driven payloads.** The properties DCR currently declares for each entity are read from the
  bank's dynamic resource docs and every submit payload is trimmed to them, so a field change on DCR does
  not need a release. Reading the docs takes tens of seconds, so the catalogue is cached for six hours and
  warmed at container start (`manage.py warm_dcr_cache`).
- **Vocabularies.** Countries, sustainable development goals, technologies/practices/processes and
  co-benefits are read from DCR's public lists (`.../banks/ogcr/public/<entity>`, no token needed) and
  served by `GET /api/reference/vocabularies/`; countries fall back to the built-in ISO list.
  Certification schemes prefer the public list as well.
- **Permissions.** `GET /api/auth/dcr/permissions/` asks DCR role by role
  (`/banks/<bank>/my/roles/<role>`) whether the user can submit, read verification status and read
  reference data. Readiness reports missing roles as a warning and submit refuses with 403 before
  writing anything to DCR.

Frontend integration: [docs/frontend-guide-v4.md](docs/frontend-guide-v4.md). Partner (verification platform)
access: [docs/partner-api.md](docs/partner-api.md).

## Data model and the local-first rule

Everything is authored and stored in the local PostGIS database. The only call that reaches DCR
automatically is user registration/login. Operators, projects and plans are pushed to DCR
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
| `parcels.Parcel` | `parcel` | belongs to an operator; MultiPolygon (EPSG:4326), IACS/LPIS codes, cadastral reference; registered on DCR by the certification platform, matched back by cadastral reference |
| `parcels.ParcelOwner` | `parcel_owner` (+ `supporting_document`) | owner declared by the operator, with the deed or extract; sent once the parcel is registered on DCR (only this platform writes it, the certification platform does not) |
| `parcels.RegistryOwner` | - | French parcels held by legal persons (DGFiP open data, metropolitan France, one vintage), matched by cadastral id and returned to partners as `registry_owners`; loaded with `load_registry_owners` |
| `parcels.ParcelOwnerVerification` | `parcel_owner_verification` | one per parcel; read from DCR only (written by the certifier/POV) |
| `parcels.ProjectParcel` | `parcel_activity` (written by the certification platform) | links a parcel to a project; verification state mirrored from `activity_verification` |

Every mirrored record carries `dcr_id`, `dcr_sync_status` (`local` / `synced` / `failed`),
`dcr_synced_at` and the last `dcr_response`. Submit creates entities in dependency order
(operator -> membership -> activity -> activity_plan -> monitoring_plan -> link records). Parcels are not
pushed: the certification platform reads the parcels of submitted activities (geometry, cadastral
reference, documents) from the partner API and registers `parcel` and `parcel_activity` on DCR itself;
the status refresh matches those records to local parcels by cadastral reference and keeps their DCR
ids. The activity boundary is the drawn geometry or, without one, the union of the linked parcels.
Owner verifications are never pushed either: the certifier or POV component writes them. The owner the
operator declares is: once the parcel is registered, submit and the status refresh write `parcel_owner`
and its deed as `supporting_document` (id `<prefix>_sd_<sha256 of the partner download URL>`, the
scheme the certification platform uses, so both arrive at the same record; `PUBLIC_API_URL` sets the
address used in that URL). The certification platform links the documents it reads at submit to
the `parcel_owner_verification` it creates; the status refresh links the parcel's documents added
later (`supporting_document` + `parcel_owner_verification_supporting_document`, id `<prefix>_sdlink_...`)
and skips links already there. A parcel gone from DCR (a registry reset) is local again at the
next submit: its owner is sent again once the parcel is re-registered, and the owner verification and
verdicts mirrored from the old records are dropped. After submission,
`POST /api/projects/{id}/dcr-status/` reads `activity_verification` (one verdict per parcel and activity),
the legacy `activity_parcel_verification`, `parcel_owner_verification`, `certificate_of_compliance` and
`audit_report` (written by the certification platform) from DCR and mirrors them (also every 15
minutes for all submitted activities, `manage.py sync_dcr_status`, as the service account):
all parcels `verified` -> `accepted`, any `failed` -> `rejected`;
on a DCR error the failing step is recorded and the project stays editable. DCR expects the client to
supply entity ids, so prefixed ids (`op_`, `act_`, `ap_`, `mp_`, `parcel_`, ...) are generated before
the first create and kept on later updates. Practices, co-benefits and SDGs that are DCR vocabulary
ids are sent as link records (`activity_technologies_practices_processes`, `activity_co_benefit`,
`activity_sustainable_development_goal`; singular entity names are picked up when DCR renames them) and
media links as `activity_media`; the ids DCR assigns are kept in
`Project.dcr_links`, so a resubmit only creates and deletes the differences.

## Documents

Supporting files (proof of ownership, cadastral extract, land use agreement, methodology, monitoring
report, photo, map, other) are attached to exactly one project or parcel and stored in the private
`media` volume; the API serves them only through the authenticated download endpoint. Each upload
records original name, content type, size and SHA-256. A new upload against an existing document
creates version n+1 and supersedes the previous one (history stays; `?all_versions=1` lists it).
Documents stay on this platform (certifiers fetch them through the partner API during the audit), so
they can still be added, and get new versions, after submit, until the certifier's verdict is final
(`verified`/`failed`) or a certificate is in force; then they are frozen. Nothing is deleted after
submission. Forwarded documents are frozen.
Limits: `DOCUMENT_MAX_SIZE_MB` (25) and `DOCUMENT_ALLOWED_EXTENSIONS`. Forwarding to the external
verification platform is prepared (`forward_status`, `forwarded_at`, `forward_reference`) but the
target is not defined yet.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health/` | liveness/readiness (checks the database) |
| GET | `/api/reference/`, `/api/reference/countries/` | vocabularies (activity/unit types, statuses, document kinds) and countries (from DCR); public |
| GET | `/api/reference/vocabularies/` | DCR vocabularies: countries, SDGs, practices, co-benefits; public |
| POST | `/api/auth/register/` | register on DCR and mirror locally |
| POST | `/api/auth/validate-email/` | confirm the e-mail with the DCR token |
| POST | `/api/auth/resend-validation-email/` | DCR sends the validation e-mail again |
| GET | `/api/auth/password-policy/` | DCR password rules; public |
| POST | `/api/auth/password-reset/` | DCR sends a reset link (needs the service account; the DCR account is looked up by e-mail) |
| POST | `/api/auth/login/` | DirectLogin proxy to DCR |
| POST | `/api/auth/logout/` | drops the cached token |
| GET | `/api/auth/me/` | current user with `dcr` block (DCR account, roles, capabilities) |
| PUT | `/api/auth/me/mobile-phone/` | set the mobile phone number on DCR |
| GET | `/api/auth/dcr/permissions/` | needed DCR roles checked live on DCR |
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
| GET/PUT/PATCH | `/api/parcels/{id}/owner/` | owner declared by the operator (`legal_name`, `owner_type`, `country_code`, `document`) |
| GET/PUT/PATCH | `/api/parcels/{id}/owner-verification/` | owner verification of the parcel |
| GET | `/api/parcels/{id}/registry-owners/` | owners known to the land registry (France, legal persons), with `matches_declared` |
| GET/POST | `/api/documents/` | supporting documents; multipart upload with `file`, `kind`, `title`, `project` or `parcel` |
| GET | `/api/documents/{id}/download/` | the file (authenticated; files are never served statically) |
| POST | `/api/documents/{id}/versions/` | upload a new version (previous one is kept, superseded) |
| PATCH/DELETE | `/api/documents/{id}/` | edit metadata / delete (blocked once the parent is submitted or forwarded) |
| GET | `/api/projects/{id}/readiness/` | submission checklist: blocking errors and warnings |
| GET/POST | `/api/projects/{id}/dcr-status/` | verification outcome; POST refreshes it from DCR (accepted/rejected, parcel statuses, certificates of compliance, audit reports) |
| POST | `/api/projects/{id}/ready/` | draft -> ready |
| POST | `/api/projects/{id}/reopen/` | ready -> draft |
| POST | `/api/projects/{id}/submit/` | push to DCR; draft/ready -> submitted (locks the project) |
| POST | `/api/partner/parcels/search/` | partner API (`Authorization: Api-Key`): parcels overlapping a geometry (`min_overlap`, neighbours that only touch it are left out) with projects, owner verification and documents |
| GET | `/api/partner/activities/{activity_id}/`, `/api/partner/parcels/{parcel_id}/` | partner lookups by DCR registry id; documents also grouped by kind |
| GET | `/api/partner/documents/{id}/download/` | partner download of a document |
| GET | `/api/partner/ping/` | partner key check |
| GET | `/api/docs/` | Swagger UI (`/api/schema/` is the OpenAPI JSON) |
| - | `/admin/` | Django admin |

Project lifecycle: `draft -> ready -> submitted -> accepted | rejected`, and `submitted -> changes_requested ->
submitted` when the certifier asks for an amendment (`validation_amendment_required` /
`verification_amendment_required` on DCR): the activity is editable again and the resubmit updates the
same DCR records (parcel links are updated by the certification platform).
After `submitted` the project is read-only (PATCH/DELETE return 409). Submit returns 400 with a
`problems` list when required fields are missing, 403 with `missing_roles` when the DCR account lacks
roles (nothing is written), and 502 with the failing `step` on DCR errors.

## Running

Prerequisites: Docker + Docker Compose v2. The image installs GDAL (`gdal-bin`) for GeoDjango.

```sh
cp .env.example .env        # fill in DCR_CONSUMER_KEY / SECRET, change passwords
docker compose up -d --build
curl http://localhost:8000/api/health/
```

The container entrypoint waits for the database, runs `migrate` and `collectstatic`, warms the DCR entity
catalogue in the background, then starts gunicorn (`RUN_MIGRATIONS=0` / `COLLECT_STATIC=0` /
`WARM_DCR_CACHE=0` disable those steps).

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
  3000/hour, login 10/min, registration 5/min, password reset and validation e-mail resend 5/min.
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
apps/core/         health and reference endpoints, shared HTTP exceptions (409/502/503)
apps/accounts/     custom User (mirror of the DCR user), sync service
apps/dcr/          DCRClient (OBP), DirectLogin authentication, onboarding, roles, vocabularies, error mapping
apps/operators/    Operator + membership (DCR operator / user_operator_relationship)
apps/projects/     Project, ActivityPlan, MonitoringPlan, DCR mapping, submit service, ViewSet
apps/parcels/      Parcel (PostGIS), owner verification, project-parcel links
apps/documents/    versioned supporting documents with private download
apps/partners/     API keys and the partner endpoints for the verification platform
docker/            entrypoint, wait_for_db, gunicorn config
```

## Next steps

- First end-to-end submit on the bank-level model.
- Store vocabulary ids on activities and push the join entities (practices, co-benefits, SDGs);
  `activity_media`.
- User invitations through DCR once the invitation roles are granted.
