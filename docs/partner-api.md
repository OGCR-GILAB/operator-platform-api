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
  "include_documents": true
}
```

| Field | Meaning |
|---|---|
| `geometry` | GeoJSON `Polygon` or `MultiPolygon` in WGS84 (EPSG:4326). Every parcel whose boundary intersects it is returned. |
| `include_unsubmitted` | default `false`: only parcels that belong to at least one project already submitted to the DCR registry (status `submitted`, `accepted` or `rejected`). `true` also returns parcels of draft projects. |
| `include_documents` | default `true`: attach the list of supporting documents. |

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
        "dcr_id": "5e370c2a-4189-48ba-856b-19b95aa7539b",
        "operator": {"id": 3, "legal_name": "Agreena", "email": "contact@example.com", "country_code": "DE", "dcr_id": "..."},
        "owner_verification": {"status_code": "verified", "status_message": "", "parcel_owner_legal_name": "J. Dupont", "authority": "Cadastre FR", "dcr_id": "..."},
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
- `projects[].link_status` and `amount` are the verification state of this parcel within that project
  (`in_progress`, `verified`, `failed`) and the carbon reduction calculated for it.
- `documents[].scope` is `parcel` for documents attached to the parcel and `project` for documents of
  one of the listed projects (`project` then holds that project id). Only current versions are listed.
- `kind` is one of `ownership_proof`, `land_use_agreement`, `cadastral_extract`, `methodology`,
  `monitoring_report`, `photo`, `map`, `other`.

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
| 404 | document not found |
| 429 | rate limit reached (3000 requests per hour per key); retry later |

All requests must use HTTPS. Request bodies are limited to 30 MB.

## Contact

Operator platform team, GILAB: Srđan Popović (srpp011@gmail.com).
