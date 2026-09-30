# Frontend integration guide (v3)

Version 3, 2026-09-18. Changes since v2: `dcr` block on `auth/me/` (DCR account, roles, what the
account may do), `operators/{id}/dcr/` (live operator record from DCR with the registry wallet
address), certification schemes carry `source` (`dcr` or `sample`). Registration now goes through
`auth/register/` on the frontend, as requested.

Base URL: `https://api-operator.gilab.rs/api/` (OpenAPI: `/api/schema/`, Swagger UI: `/api/docs/`).
All responses are JSON; lists are paginated (`{count, next, previous, results}`, `?page=`, 25 per page)
except where noted. Errors come as `{"detail": "...", "code": "..."}` or, for validation, a field map.

## DCR data available to show right now

Most registry data (verification verdicts, tCO2e amounts, parcel statuses) arrives only after DCR
grants the submit roles; `projects/{id}/dcr-status/` is ready for that. Two things are real today:

- **`GET auth/me/` -> `dcr`**: the account as DCR knows it (`user_id`, `username`, `email`,
  `provider`, `provider_id`), the full list of `roles`, `synced_at`, and `capabilities` with three
  entries: `submit`, `status`, `reference`. Each has `allowed` (bool) and `missing_roles` (list).
  Use it for a "DCR account" card and to explain up front why submit is not possible yet
  (`capabilities.submit.missing_roles`), instead of letting the user hit a 502 on click.
  `dcr` is `null` for local-only accounts (admin). The block refreshes on login and whenever the
  token is re-validated (every few minutes).
- **`GET operators/{id}/dcr/`**: the operator record as stored on DCR, read live:
  `{dcr_id, dcr_synced_at, fetched_at, record, ogcr_wallet_address}`. `record` is DCR's own object
  (`operator_id`, `legal_name`, address fields, `ogcr_wallet_address`); `ogcr_wallet_address` is the
  registry-assigned wallet, worth showing on the operator profile. `404` until the operator has been
  registered on DCR, which happens on the first project submit (the operator and the membership are
  registered even when the activity step is still refused for missing roles). `502` with
  `step: "operator"` if DCR cannot be read.

## Authentication

Accounts live on the DCR platform. The API proxies DCR so the frontend never talks to DCR directly.

1. Register: `POST auth/register/` `{email, password, first_name, last_name, username?}`.
   DCR password policy: at least 10 characters with upper/lower case, digits and a special character,
   or longer than 16. Errors from DCR come back with their message and `code: "dcr_rejected"`.
2. If DCR sent a validation e-mail, the user opens the link and the frontend posts the token:
   `POST auth/validate-email/` `{token}`.
3. Login: `POST auth/login/` `{username, password}` -> `{token, user}`.
4. Send the token on every call: `Authorization: DirectLogin token="<token>"` (or simply
   `Authorization: Bearer <token>`; both are accepted).
   Store it in memory or sessionStorage; `POST auth/logout/` drops the server-side cache.
   `401` means the token is invalid or expired: go back to login. `503` means DCR is unreachable.
5. `GET auth/me/` returns the current user. `POST auth/password-reset/` `{email}` asks DCR to send a
   reset link (answers 503 until the DCR service account is configured).

## The local-first rule

Everything the user edits (operators, projects, plans, parcels, documents) lives only on this API until
the user presses **Submit** on a project. Only then is it pushed to DCR, in one call. Every mirrored
record exposes `dcr_id`, `dcr_sync_status` (`local` / `synced` / `failed`) and `dcr_synced_at`.

## Order of operations

1. **Operator** (the legal entity running the activity): `POST operators/`
   `{legal_name, email, country_code, phone?, address_line_1?, address_line_2?, postcode?}`.
   The creator becomes a member; add colleagues with `POST operators/{id}/members/`
   `{username | email, relationship}` (they must have logged in once).
2. **Project** (DCR "activity"): `POST projects/` with at least `{name, operator, start_date, end_date, type}`.
   `type` is one of `CARBON_FARMING`, `PERMANENT_REMOVAL`, `CARBON_STORAGE_IN_PRODUCTS`; if you send
   `unit_types` (one of the four values from `GET reference/`), `type` is derived automatically.
   Optional: `summary`, `description`, `website`, `image`, `media_links[]`,
   `technologies_practices_processes[]`, `cobenefits[]`, `city`, `country_code`, `geometry` (GeoJSON),
   `term_commitment`, `methodologies`, `monitoring_period_*`, `certification_scheme_id/_name`.
   Certification schemes come from `GET auth/dcr/certification-schemes/`. Each row has `source`:
   `dcr` for real registry data, `sample` for the built-in placeholder list served while DCR does not
   expose schemes yet (show a small badge; the shape is identical, so nothing changes later).
3. **Activity plan**: `PUT projects/{id}/plan/` (creates or replaces; `PATCH` for partial updates).
4. **Monitoring plan**: `PUT projects/{id}/monitoring-plan/`.
   `monitored_data_parameters` is a list of `{name, unit, scope}`.
5. **Parcels**: `POST parcels/` `{operator, geometry, name?, cadastral_reference?, iacs_codes[], lpis_codes[]}`,
   then link: `POST projects/{id}/parcels/` `{parcel, amount?, status_message?}`;
   unlink: `DELETE projects/{id}/parcels/{parcel_id}/`.
   Owner verification: `PUT parcels/{id}/owner-verification/` `{status_code, authority, parcel_owner_legal_name?}`.
6. **Documents**: multipart `POST documents/` with `file`, `kind`, `title`, and `project` or `parcel`.
   Download through `GET documents/{id}/download/` (send the auth header; do not link the raw URL).
   New version: multipart `POST documents/{id}/versions/` with `file`.
7. **Readiness**: `GET projects/{id}/readiness/` -> `{ready, editable, errors[], warnings[], checks[]}`.
   Show it as a checklist; `errors` block submit, `warnings` do not.
8. **Submit**: `POST projects/{id}/submit/`. Success returns the project with `status: "submitted"`
   and DCR ids. `400` returns `{detail, problems[]}` (same as readiness errors). `502` returns
   `{detail, step}` where `step` names the DCR entity that failed (`operator`, `activity`,
   `activity_plan`, `parcel`, ...); the project stays editable and can be resubmitted.
   After submit the project, its plans, parcels and documents are read-only (`409` on changes).

9. **After submit**: `GET projects/{id}/dcr-status/` shows the last known outcome; `POST` the same URL to
   refresh it from DCR (the certifier's verdict moves the project to `accepted` or `rejected` and updates
   each parcel's `status_code`, `amount` and owner verification). Poll on page load, not continuously.

Project lifecycle: `draft -> ready -> submitted -> accepted | rejected`
(`POST projects/{id}/ready/`, `POST projects/{id}/reopen/`).

## Reference data

`GET reference/` returns the vocabularies to use in selects: activity types, unit types (with the
activity type each implies), project and verification statuses, document kinds, example practices and
co-benefits (free text is accepted for those two until DCR publishes a list), and the document limits.
`GET reference/countries/` lists ISO 3166-1 alpha-2 codes with names; `country_code` fields are
validated against it.

## Geometry

Send GeoJSON `Polygon` or `MultiPolygon` in any SRID (WGS84 assumed when unspecified); the API stores
and returns `MultiPolygon` in EPSG:4326. `GET parcels/geojson/` returns a `FeatureCollection` with
`id`, `name`, `cadastral_reference`, `operator`, `area_ha` and `dcr_sync_status` properties, ready
for a MapLibre source. Invalid geometries (self-intersections) are rejected with a message.

## Filtering and search

List endpoints accept `?search=`, `?ordering=field` / `-field`, and field filters, e.g.
`projects/?status=draft`, `parcels/?operator=3`, `documents/?parcel=7&kind=ownership_proof`.

## HTTP status summary

| Code | Meaning |
|---|---|
| 400 | validation error (field map) or DCR rejected the data (`dcr_rejected`) |
| 401 | not logged in / token expired / wrong DCR credentials |
| 404 | not found or not yours |
| 409 | not allowed in the current state (submitted project, last operator member, ...) |
| 502 | DCR returned an error during submit (`step` says where) |
| 503 | DCR unreachable or a required DCR configuration is missing |
