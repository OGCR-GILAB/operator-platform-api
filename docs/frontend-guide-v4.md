# Frontend integration guide (v4)

Version 4, 2026-09-23, with addenda of 2026-09-24, 2026-10-12, 2026-10-14, 2026-10-20, 2026-10-28, 2026-11-12 (two) and 2026-11-13. Previous: v3 (2026-09-18).

## What changed since v3 (action needed)

1. **`dcr_id` no longer means "registered on DCR".** DCR now expects the client to supply entity ids,
   so the API generates them (`act_…`, `op_…`, `parcel_…`, `ap_…`, `mp_…`) *before* the first submit
   attempt. After a failed submit a project can therefore carry a `dcr_id` while nothing exists on
   DCR. Use `dcr_sync_status` for badges and logic: `synced` = registered on DCR, `failed` = last
   attempt failed (see `step`), `local` = never sent. Show `dcr_id` as an identifier only.
   Deleting and editing rules on the API follow the same status, not the id.
2. **Activity plan tonnes are decimals.** `expected_total_carbon_removals`,
   `expected_total_soil_emissions`, `expected_total_ghg_emissions_associated` and
   `expected_net_benefit` accept and return numbers with up to 3 decimals (e.g. `6.5`), no longer
   integers only. Inputs should allow fractions; values come back as JSON numbers.
3. **`certification_scheme_id` is what DCR wants.** Keep sending both `certification_scheme_id` and
   `certification_scheme_name` on the project, but the id is the one forwarded to DCR; a project with
   only the name will be registered without a scheme. Sample schemes have ids starting with
   `cs_sample_`; real ones will replace them transparently.
4. **Documents reach DCR through the verifications.** On submit, `ownership_proof` documents of a
   parcel are attached to its owner verification, and all current documents of the parcel and the
   project to the parcel–activity verification (metadata + a download link for verifiers). No new
   endpoint; it only makes uploading documents *before* submit matter.
5. **The DCR payload adapts to DCR's live schema.** Fields DCR does not declare are dropped
   automatically, so a DCR-side change no longer requires an API release. Nothing to do on the
   frontend; mentioned so `dcr_response` contents are not surprising.

Unchanged: all endpoints, auth, readiness, dcr-status, documents, parcels, reference data.

### Addendum 2026-09-24 (TESOBE field sheet update)

- **Owner verification is no longer pushed to DCR on submit.** The operator role bundle only reads
  `parcel_owner_verification`; the certifier or the POV component writes it. The local form stays as
  preparation data and is overwritten by `dcr-status` once DCR has a verdict. It is therefore never
  frozen by a submit any more (`dcr_sync_status` stays `local` until mirrored).
- **Parcel–project link on DCR becomes `parcel_activity`** (no status, no amount). Verification per
  parcel comes back through `dcr-status` from `activity_verification`; the project turns `accepted`
  only when every parcel is `verified`, `rejected` if any parcel `failed`.
- **New project field `hero_image`** (URL), next to `image`.
- The sheet also introduces vocabularies for co-benefits, practices and SDGs, an `activity_media`
  entity and `supporting_document` entities; the API will expose them once DCR declares them
  (separate announcement).

### Addendum 2026-10-12 (DCR API v7.0.0)

The API now calls DCR **v7.0.0** and the bank-level entity model (`banks/ogcr`). Existing endpoints
and response shapes are unchanged; the items below are additions.

1. **DCR account card.** `GET auth/me/` -> `dcr` has new fields: `api_version` (`"v7.0.0"`),
   `bank_id` (`"ogcr"`), `mobile_phone_number`, `mobile_phone_number_is_validated`. Roles granted at
   `bank_id` now count (they are listed in `roles`); `bank_scoped_roles` still lists every grant per
   bank, so only grants at *other* banks are worth flagging.
2. **Vocabularies from DCR.** `GET reference/vocabularies/` (public) returns `countries`,
   `sustainable_development_goals` (with an `icon` URL), `technologies_practices_processes` and
   `co_benefits`, each a list of `{value, label}`, plus `sources` per list (`dcr`, `local` fallback,
   or `unavailable`). `value` is the DCR id to store, e.g. `AGROFORESTRY`. `GET reference/countries/`
   keeps its `{code, name}` shape but now comes from DCR (249 entries). Use these lists for the
   practices and co-benefits selects instead of the example values; `co_benefits` currently holds only
   a DCR placeholder (`sample`), so keep free text there until DCR fills it.
3. **Live permission check.** `GET auth/dcr/permissions/` checks each needed role on DCR and returns
   `{source, bank_id, checked_at, roles[], capabilities}`; `capabilities.submit|status|reference` has
   `allowed`, `missing_roles`, `unknown_roles`. Cached 5 minutes, `?refresh=1` re-checks.
   `source: "profile"` means DCR could not answer and the login-time role list was used.
   Readiness gains a `dcr.roles` warning when submit roles are missing, and **submit now answers
   `403`** `{detail, step: "permissions", bank_id, missing_roles[]}` *before* writing anything to DCR.
4. **Password rules.** `GET auth/password-policy/` (public) returns DCR's rules:
   `{description, policies: [{description, min_length, max_length, required_character_classes: [{name, regex}], regex}]}`.
   A password is valid when it matches **any one** policy. Show `description` under the field and
   validate as the user types; `POST auth/register/` now rejects a weak password with `400` on
   `password` before calling DCR. `503` when DCR cannot report the policy (then just let the user
   submit; DCR still checks).
5. **Resend the validation e-mail.** `POST auth/resend-validation-email/` `{email, username?}`
   (public, rate limited) -> always `202`. Offer it on the "check your inbox" screen and when login
   fails because the e-mail is not validated.
6. **Mobile phone.** `POST auth/register/` accepts optional `mobile_phone_number` in international
   format (`+33 6 12 34 56 78`). A logged-in user can set it with `PUT auth/me/mobile-phone/`
   `{mobile_phone_number}`, which returns the refreshed `me`. `400` on a malformed number.

### Addendum 2026-11-13 (registry owners next to the declared owner)

The certification platform asked for the owner known to the land registry next to the owner the
operator declares, so the auditor can compare the two. For French parcels we hold the DGFiP open file
of parcels with a right held by a **legal person** (companies, GAEC/GFA/SCI, municipalities, the
State, ...; metropolitan France, situation on 1 January 2026). Owners who are natural persons are not
published by the registry.

- `GET /api/parcels/{id}/registry-owners/` returns
  `{source, source_url, vintage, coverage, cadastral_reference, owners, matches_declared}`;
  `404` when the parcel has no French cadastral reference (or no registry is loaded).
- `owners[]`: `{legal_name, siren, legal_form, person_group, right_code, right_label,
  suggested_owner_type}`. `right_code` `P` is the owner; others are rights next to it (`U`
  usufructuary, `N` bare owner, `G` manager, ...). `suggested_owner_type` is `legal_person` or
  `public_entity`, ready for the declaration form.
- `matches_declared`: `true` when the declared owner's name is one of the registry owners (`P`),
  compared without case, accents or punctuation; `false` when it is not; `null` when there is nothing
  to compare (no declaration yet, or no legal-person owner in the registry).
- An empty `owners` list is normal for most farmland (private owners) and is not an error.
- Parcels outside metropolitan France (no French cadastral reference, or overseas departments) get
  `404`: the registry does not cover them, so hide the section.
- Parcel rows (`/api/parcels/` and `parcel_detail` in an activity's parcel links) carry
  `owner_is_complete` (`true`/`false`, `null` while nothing is declared) and `registry_match` (the same
  value as `matches_declared`, computed once per response), so the table needs no extra calls.

### Addendum 2026-11-12b (declared parcel owner)

DCR has a new `parcel_owner` record: the owner **as declared by the operator**, with the deed or
cadastre extract it rests on. The certifier then verifies it (`parcel_owner_verification`, read-only
for us as before). We write the declaration; the frontend needs a small form per parcel.

- `GET /api/parcels/{id}/owner/` returns the declaration, `404` while there is none.
- `PUT` (or `PATCH`) the same URL with
  `{legal_name, owner_type, country_code, document}`:
  - `owner_type`: `natural_person`, `legal_person` or `public_entity`
  - `country_code`: ISO 3166-1 alpha-2 of the owner's address (same list as `reference/countries/`)
  - `document`: id of a **current** document uploaded **to this parcel** (normally kind
    `ownership_proof` or `cadastral_extract`); `null` is accepted while drafting.
- Response: the fields above plus `document_title`, `is_complete`, `dcr_id`, `dcr_sync_status`.
- Editable until the parcel's verification is final (`409` after that), also after submit.
- It reaches DCR once the certification platform has registered the parcel (within minutes after
  submit; the status refresh does it). A change after that is sent immediately, as an update.
  So `dcr_sync_status` reads `local` until then: show it as "waiting for registration", not as an error.
- Parcel list rows have `has_owner`. Readiness warns per linked parcel without a complete declaration
  (`parcel.<id>.owner`, a warning, not a blocker).
- `dcr-status` parcels carry `owner`: `{legal_name, owner_type, country_code, document, is_complete,
  dcr_id, dcr_sync_status, error}`; `error` is set when sending failed.

### Addendum 2026-11-12 (DCR schema changes)

- **Every parcel needs a cadastral reference.** DCR now requires `cadastral_reference` on parcels, so
  readiness reports an error per parcel without one (`parcel.<id>.cadastral_reference`). Parcels picked
  from the cadastre have it; parcels drawn by hand must get it typed in.
- **Parcels are registered on DCR by the certification platform, not by us.** Submit sends the
  operator, activity, plans and links; the certification platform reads the activity's parcels (with
  cadastral references and documents) from our partner API and writes `parcel` and `parcel_activity`
  itself, under its own ids. The activity boundary sent on submit is the drawn geometry or, if none was
  drawn, the outline of the linked parcels.
- **A parcel's `dcr_id` and `dcr_sync_status`** are filled in by the status refresh once the
  certification platform has registered it (matched by cadastral reference). Until then a parcel of a
  submitted activity shows `local`; do not present that as an error ("waiting for registration").
- Unlinking a parcel from an activity reopened for amendment only changes it here; the certification
  platform updates its links on DCR.
- **Activities that disappear from DCR** (e.g. a registry reset) are noticed by the status sync: the
  activity goes back to `ready`, its mirrored verdicts are cleared, and the operator can submit it again
  with the same ids.
- SDG links are written to DCR's new `activity_sustainable_development_goal` entity (nothing changes in
  the API). DCR is renaming plural entities to singular ones; the backend picks whichever name DCR
  declares, so nothing changes for the frontend.

### Addendum 2026-10-28 (amendment requests)

The certification platform can now ask the operator to change a submitted activity, at validation
(`validation_amendment_required`) or at verification (`verification_amendment_required`).

- The activity then gets **status `changes_requested`** (picked up by the 15-minute sync or a
  `POST dcr-status`). `dcr-status` has an `amendment` block while it lasts:
  `{stage: "validation" | "verification", status_code, message}`; `message` is what the certifier
  wrote. Show it prominently on the activity, with a call to action.
- In `changes_requested` the activity is **editable again** (`is_editable: true`): details, plans,
  parcels (link and unlink) and documents (upload, new versions; no deletes after the first submit).
- The operator resubmits with the usual `POST submit/` (no "mark ready" step). The same DCR records are
  updated; parcels removed from the activity also lose their link on DCR. The status returns to
  `submitted` and the request counts as answered until the certifier acts again.
- `submitted_at` keeps the date of the first submit.

### Addendum 2026-10-20 (vocabulary links, media, automatic status sync)

- **New project field `sustainable_development_goals`**: a list of DCR ids from
  `GET reference/vocabularies/` -> `sustainable_development_goals` (17 values, each with an `icon`).
  Offer it as a multi-select next to practices and co-benefits.
- **Practices, co-benefits and SDGs reach DCR as links on submit.** Values that are DCR vocabulary
  ids become link records on DCR; other (free text) values are kept on the project and sent only as
  text. Readiness reports them as a warning with code `<field>.vocabulary`, e.g.
  `technologies_practices_processes.vocabulary`. All three fields must be lists of strings (`400`
  otherwise); duplicates are removed.
- **`media_links` items can be objects**: `{link, title?, type?, author?, published_date?, image?}`
  (`link` and `image` URLs, `published_date` `YYYY-MM-DD`); plain URL strings still work. Each item is
  sent to DCR as an `activity_media` record (type `link` and the URL as title when not given).
- **`dcr-status` refreshes itself.** The server re-reads the verification, certificates and audit
  reports of every submitted activity every 15 minutes, so a plain `GET projects/{id}/dcr-status/`
  is enough on page load; keep `POST` for an explicit "refresh now" button. `checked_at` tells how old
  the data is.

- **`dcr-status` -> `parcels[]`** also has `name`, `cadastral_reference` and `has_verdict`:
  `true` only when the certifier recorded a verdict for that parcel itself. Today the certification
  platform verifies per activity (`parcel_id` empty), so parcels show `has_verdict: false` and the
  activity's `verification` applies to them. Verification statuses now include `validated`.

- **Documents after submit.** Uploads and new versions are accepted after submit until the
  verification is final (`verified`/`failed`) or a certificate is in force; documents then have
  `is_frozen: true`. Deleting is refused (`409`) once the activity is submitted; each document has
  `is_deletable` to hide the delete action. Uploading to a closed activity or parcel gives `400`.

- **Verification status codes** (as the certification platform writes them): `draft`,
  `under_review`, `validated`, `denied` (failed at validation), `in_progress`, `verified`, `failed`.
  `denied` and `failed` move the activity to `rejected`, `verified` to `accepted`. Two more are
  coming: `validation_amendment_required` and `verification_amendment_required`.

### Addendum 2026-10-14 (certificates and audit reports)

The certification platform now writes its results to DCR. `GET|POST projects/{id}/dcr-status/` returns,
next to `verification` and `parcels`:

- `certificate`: the newest certificate of compliance with `certification_status` `certified` or
  `active` (or `null`). Worth showing on the project: status, `issue_date`, `expiry_date`,
  `unit_types` and the benefit amounts (`permanent_net_carbon_removal_benefit`,
  `carbon_farming_net_soil_emission_reduction_benefit`, ...).
- `certificates`: all certificates, newest first (`expired` / `suspended` ones included, for history).
- `audit_reports`: audit reports, newest first (`audit_date`, `audit_report_issue_date`,
  `audit_report_summary`, `non_conformities_list`, `certification_body_id`, ...).

These are DCR's own records, passed through unchanged; many fields can be `null`. Verification
`status_code` values now also include `pending` and `under_review` (both keep the project `submitted`);
`GET reference/` lists them in `verification_statuses`.

## DCR data available to show right now

Most registry data (verification verdicts, tCO2e amounts, parcel statuses) arrives only after DCR
grants the submit roles; `projects/{id}/dcr-status/` is ready for that. Two things are real today:

- **`GET auth/me/` -> `dcr`**: the account as DCR knows it (`user_id`, `username`, `email`,
  `provider`, `provider_id`), the list of system-level `roles` (the ones that count for OGCR entities),
  `bank_scoped_roles` (grants per bank, which DCR ignores for these entities; shown so a mis-scoped
  grant is visible), `synced_at`, and `capabilities` with three
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
   `activity_plan`, `monitoring_plan`, ...); the project stays editable and can be resubmitted.
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
`GET reference/countries/` lists ISO 3166-1 alpha-2 codes with names (from DCR, built-in list as a
fallback); `country_code` fields are validated against it. `GET reference/vocabularies/` returns the
DCR vocabularies (countries, SDGs, practices, co-benefits); see the 2026-10-12 addendum.

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
| 403 | submit refused before any DCR write: the DCR account lacks roles (`missing_roles`) |
| 404 | not found or not yours |
| 409 | not allowed in the current state (submitted project, last operator member, ...) |
| 502 | DCR returned an error during submit (`step` says where) |
| 503 | DCR unreachable or a required DCR configuration is missing |
