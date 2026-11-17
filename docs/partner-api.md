# OGCR Operator Platform - Partner API

Read-only access for verification partners to the parcels, projects and supporting documents held
on the OGCR operator platform.

- Base URL: `https://api-operator.gilab.rs/api/partner/`
- Machine-readable overview: `GET https://api-operator.gilab.rs/api/partner/` (JSON index)
- Interactive documentation of every endpoint: `https://api-operator.gilab.rs/api/docs/`
  (section "partner"; use the **Authorize** button with `Api-Key <your key>`)

A ready-made Postman collection is available at
`https://codeberg.org/OGCR/operator-platform-api/src/branch/main/docs/partner-api.postman_collection.json`
(set the `api_key` variable after import).

## Authentication

You receive an API key from the operator platform team. Send it on every request:

```
Authorization: Api-Key ogcr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Keep the key on your server side only (environment variable or secret store), never in browser
code or in URLs. The key does not expire; ask us to rotate or revoke it at any time. It grants read
access to parcels and documents of all operators, and nothing else on the platform.

Check that the key works:

```
GET https://api-operator.gilab.rs/api/partner/ping/
-> {"partner": "verification-platform", "status": "ok"}
```

## Search parcels by area

```
POST https://api-operator.gilab.rs/api/partner/parcels/search/
Content-Type: application/json
Authorization: Api-Key ogcr_...

{
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[13.40, 52.51], [13.41, 52.51], [13.41, 52.53], [13.40, 52.53], [13.40, 52.51]]]
  },
  "include_unsubmitted": false,
  "include_documents": true,
  "min_overlap": 0.01
}
```

| Field | Meaning |
|---|---|
| `geometry` | GeoJSON `Polygon` or `MultiPolygon` in WGS84 (EPSG:4326). Parcels that overlap it are returned (see `min_overlap`). |
| `include_unsubmitted` | default `false`: only parcels that belong to at least one project already submitted to the DCR registry (status `submitted`, `accepted` or `rejected`). `true` also returns parcels of draft projects. |
| `include_documents` | default `true`: attach the list of supporting documents. |
| `min_overlap` | default `0.01`: the overlap must cover at least this share of the smaller of the two areas (the parcel or the search geometry). Neighbouring parcels that only share a border with the geometry, or overlap it by a sliver, are left out, so searching with an activity's outline returns that activity's parcels and not the fields next to it. A small search area inside a parcel still finds the parcel. `0` returns every parcel that intersects the geometry, including ones that only touch it. |

The response is a GeoJSON `FeatureCollection`:

```json
{
  "type": "FeatureCollection",
  "count": 1,
  "include_unsubmitted": false,
  "features": [
    {
      "type": "Feature",
      "id": 12,
      "geometry": {"type": "MultiPolygon", "coordinates": [[[[13.405, 52.52], [13.406, 52.52], [13.406, 52.521], [13.405, 52.52]]]]},
      "properties": {
        "id": 12,
        "name": "Field north",
        "cadastral_reference": "75056000AB0001",
        "iacs_codes": ["DE-BB-IACS-2026-0012345678"],
        "lpis_codes": [],
        "area_ha": 0.38,
        "overlap": 1.0,
        "dcr_id": "5e370c2a-4189-48ba-856b-19b95aa7539b",
        "operator": {"id": 3, "legal_name": "Agreena", "email": "contact@example.com", "country_code": "DE", "dcr_id": "..."},
        "owner_verification": {"status_code": "verified", "status_message": "", "parcel_owner_legal_name": "J. Dupont", "authority": "Cadastre FR", "dcr_id": "..."},
        "declared_owner": {"legal_name": "J. Dupont", "owner_type": "natural_person", "country_code": "FR", "document": 4, "dcr_id": "..."},
        "registry_owners": {
          "source": "DGFiP - Fichiers des parcelles des personnes morales", "source_url": "https://www.data.gouv.fr/datasets/...",
          "vintage": 2026, "coverage": "Metropolitan France: parcels with a right held by a legal person; natural persons are not published.",
          "cadastral_reference": "75056000AB0001",
          "owners": [{"legal_name": "GFA DES VARENNES", "siren": "424108801", "legal_form": "GFA",
                      "person_group": "Personnes morales non remarquables", "right_code": "P", "right_label": "Propriétaire",
                      "suggested_owner_type": "legal_person"}],
          "matches_declared": false
        },
        "projects": [
          {"id": 7, "name": "Brandenburg Regenerative Wheat", "type": "CARBON_FARMING", "status": "submitted",
           "dcr_id": "...", "start_date": "2027-01-01", "end_date": "2031-12-31", "submitted_at": "2026-09-16T10:00:00Z",
           "link_status": "in_progress", "amount": 6}
        ],
        "documents": [
          {"id": 41, "scope": "parcel", "project": null, "kind": "ownership_proof", "title": "Deed", "description": "",
           "version": 2, "original_name": "deed.pdf", "content_type": "application/pdf", "size": 183422,
           "sha256": "39677cb1...", "uploaded_at": "2026-09-16T09:12:00Z",
           "download_url": "https://api-operator.gilab.rs/api/partner/documents/41/download/"}
        ]
      }
    }
  ]
}
```

Notes on the properties:

- `area_ha` is computed in ETRS89-LAEA (EPSG:3035).
- `overlap` (search only) is the share of the smaller of the two areas, parcel or search geometry,
  covered by their overlap: `1.0` for a parcel inside the geometry, near `0` for a neighbour.
- `projects[].link_status` and `amount` are the verification state of this parcel within that project
  (`pending`, `under_review`, `in_progress`, `verified`, `failed`) and the carbon reduction calculated for it.
- `documents[].scope` is `parcel` for documents attached to the parcel and `project` for documents of
  one of the listed projects (`project` then holds that project id). Only current versions are listed.
- `documents_by_kind` holds the same document rows grouped by `kind`.
- `declared_owner` is the owner as the operator declared it (DCR `parcel_owner`), `null` while none.
- `registry_owners` is what the land registry says, for the auditor to compare with `declared_owner`.
  For parcels in metropolitan France (`cadastral_reference` is a 14-character IDU) it comes from the DGFiP open file
  of parcels owned by legal persons (companies, GAEC/GFA/SCI, municipalities, the State, ...), situation
  on 1 January of `vintage`. `owners` lists every holder with its right (`right_code` `P` owner,
  `U` usufructuary, `N` bare owner, `G` manager, ... as in the DGFiP file). An empty `owners` list means
  no legal person holds a right on the parcel: it is most likely owned by natural persons, whom the
  registry does not publish, so it is not a mismatch by itself. `null` means the parcel has no French
  cadastral id (another country or no reference), so there is nothing to compare.
  `matches_declared` is `true` when the declared owner's name is one of the registry owners with
  right `P` (compared without case, accents or punctuation), `false` when it is not, and `null` when
  there is nothing to compare (no declaration, or no legal-person owner in the registry).
- `kind` is one of `ownership_proof`, `land_use_agreement`, `cadastral_extract`, `methodology`,
  `monitoring_report`, `photo`, `map`, `other`.

## Look up by registry id

When you already know the DCR registry ids, fetch the data directly instead of searching by area.

### Activity

```
GET https://api-operator.gilab.rs/api/partner/activities/{activity_id}/
Authorization: Api-Key ogcr_...
```

`activity_id` is the activity id on DCR. Only activities submitted to the registry are available
(`404` otherwise). The response holds the activity, its operator, the activity-level documents and
its parcels:

```json
{
  "id": 7,
  "dcr_id": "act_9f2c41d0b6a84e5f8c1d2e3f",
  "name": "Beauce Regenerative Wheat",
  "type": "CARBON_FARMING",
  "status": "submitted",
  "start_date": "2027-01-01",
  "end_date": "2031-12-31",
  "submitted_at": "2026-10-16T09:30:00Z",
  "certification_scheme_id": "cs_01HZZ1K0X5F6Q7R8S9T0",
  "operator": {"id": 3, "legal_name": "Ferme du Val de Loire SAS", "email": "contact@example.com", "country_code": "FR", "dcr_id": "op_..."},
  "documents": [ ... ],
  "documents_by_kind": {"methodology": [ ... ], "monitoring_report": [ ... ]},
  "parcels": {
    "type": "FeatureCollection",
    "count": 4,
    "features": [ ... ]
  }
}
```

Each parcel feature has the same properties as in the area search, except that its `documents`
contain only the parcel's own documents (ownership proofs, cadastral extracts, ...); the activity's
documents are listed once at the top. Add `?include_documents=false` to leave all documents out.

### Parcel

```
GET https://api-operator.gilab.rs/api/partner/parcels/{parcel_id}/
Authorization: Api-Key ogcr_...
```

`parcel_id` is the parcel id on DCR. Returns one GeoJSON `Feature` with the same properties as in
the area search (operator, owner verification, declared and registry owners, projects, documents). Only parcels of a submitted
activity unless `?include_unsubmitted=true`; `?include_documents=false` leaves documents out.

### Documents by kind

Every list of documents also comes grouped as `documents_by_kind`, keyed by `kind`, e.g.
`documents_by_kind.ownership_proof` for the proofs of ownership of a parcel. The rows are the same
as in `documents`.

## Download a document

```
GET https://api-operator.gilab.rs/api/partner/documents/{id}/download/
Authorization: Api-Key ogcr_...
```

Returns the file as an attachment with its original name. The `X-Checksum-SHA256` response header
matches `sha256` from the search result, so downloads can be verified.

## Errors and limits

| Code | Meaning |
|---|---|
| 400 | invalid geometry or body (the message says what is wrong) |
| 401 | missing, invalid or revoked API key |
| 404 | document, activity or parcel not found (or not submitted to the registry) |
| 429 | rate limit reached (3000 requests per hour per key); retry later |

All requests must use HTTPS. Request bodies are limited to 30 MB.

## Contact

Operator platform team, GILAB: Srđan Popović (srpp011@gmail.com).
