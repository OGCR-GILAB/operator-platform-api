"""
Local model -> DCR dynamic entity payloads.

Conventions taken from the TESOBE field sheet and the DCR example bodies:
identifiers are assigned by DCR on create (never sent), empty values are omitted,
list-valued properties are sent as strings (JSON array or comma separated) because the
DCR properties are typed as string.
"""

import json
from datetime import date
from decimal import Decimal


def _clean(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if v not in (None, "", [], {})}


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _json_list(values) -> str | None:
    return json.dumps(list(values)) if values else None


def _csv(values) -> str | None:
    return ",".join(str(v) for v in values) if values else None


def _num(value):
    """Integral values as int, otherwise float (DCR accepts numbers for tonnes)."""
    if value is None:
        return None
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def documents_payload(documents) -> str | None:
    """
    Supporting documents as a JSON string: metadata plus the partner download URL.
    Verifiers fetch the files through the partner API with their key.
    """
    rows = [
        {
            "id": d.pk,
            "kind": d.kind,
            "title": d.title,
            "version": d.version,
            "file_name": d.original_name,
            "content_type": d.content_type,
            "size": d.size,
            "sha256": d.sha256,
            "url": f"https://api-operator.gilab.rs/api/partner/documents/{d.pk}/download/",
        }
        for d in documents
    ]
    return json.dumps(rows) if rows else None


def operator_payload(operator) -> dict:
    return _clean(
        {
            "legal_name": operator.legal_name,
            "email": operator.email,
            "phone": operator.phone,
            "address_line_1": operator.address_line_1,
            "address_line_2": operator.address_line_2,
            "postcode": operator.postcode,
            "country_code": operator.country_code,
            "country_id": operator.country_code,
            "ogcr_wallet_address": operator.ogcr_wallet_address,
        }
    )


def membership_payload(membership) -> dict:
    return _clean(
        {
            "user_id": membership.user.dcr_user_id,
            "operator_id": membership.operator.dcr_id,
            "relationship": membership.relationship,
            "relationship_to_operator": membership.relationship,
        }
    )


def activity_payload(project) -> dict:
    geometry = json.loads(project.geometry.geojson) if project.geometry else None
    plan = getattr(project, "plan", None)
    return _clean(
        {
            "name": project.name,
            "summary": project.summary,
            "description": project.description,
            "website": project.website,
            "image": project.image,
            "hero_image": project.hero_image,
            "media_links": _csv(project.media_links),
            "operator_id": project.operator.dcr_id if project.operator else None,
            "technologies_practices_processes": _json_list(
                project.technologies_practices_processes
            ),
            "type": project.type,
            "activity_type": project.type,
            "unit_types": project.unit_types,
            "city": project.city,
            "country_code": project.country_code,
            "country_id": project.country_code,
            "multipolygon_coordinates": geometry,
            # kept while DCR declares the back-reference; the schema filter drops it otherwise
            "activity_plan_id": plan.dcr_id if plan and plan.is_synced else None,
            "start_date": _iso(project.start_date),
            "end_date": _iso(project.end_date),
            "term_commitment": project.term_commitment,
            "cobenefits": _json_list(project.cobenefits),
            "methodologies": project.methodologies,
            "monitoring_period_years": project.monitoring_period_years,
            "monitoring_period_start_date": _iso(project.monitoring_period_start_date),
            "monitoring_period_end_date": _iso(project.monitoring_period_end_date),
            "certification_scheme": project.certification_scheme_name,
            "certification_scheme_id": project.certification_scheme_id,
        }
    )


def activity_plan_payload(plan) -> dict:
    return _clean(
        {
            "activity_id": plan.project.dcr_id,
            "date_submitted": _iso(plan.date_submitted or date.today()),
            "activity_eligibility": plan.activity_eligibility,
            "legal_parcel_ownership": plan.legal_parcel_ownership,
            "coordinate_reference_system": plan.coordinate_reference_system,
            "iacs_codes": _csv(plan.iacs_codes),
            "lpis_codes": _csv(plan.lpis_codes),
            "article_8_1_information": plan.article_8_1_information,
            "methodology_quantification_baseline": plan.methodology_quantification_baseline,
            "methodology_additionality_funding_sources": (
                plan.methodology_additionality_funding_sources
            ),
            "methodology_long_term_storage": plan.methodology_long_term_storage,
            "methodology_sustainability": plan.methodology_sustainability,
            "expected_total_carbon_removals": _num(plan.expected_total_carbon_removals),
            "expected_total_soil_emissions": _num(plan.expected_total_soil_emissions),
            "expected_total_ghg_emissions_associated": _num(
                plan.expected_total_ghg_emissions_associated
            ),
            "expected_net_benefit": _num(plan.expected_net_benefit),
            "group_advisory_services_description": plan.group_advisory_services_description,
            "group_internal_control_system_description": (
                plan.group_internal_control_system_description
            ),
        }
    )


def monitoring_plan_payload(plan) -> dict:
    return _clean(
        {
            "activity_id": plan.project.dcr_id,
            "date_submitted": _iso(plan.date_submitted or date.today()),
            "monitored_data_parameters": _json_list(plan.monitored_data_parameters),
            "monitoring_frequency": plan.monitoring_frequency,
            "emission_sources_and_sinks": plan.emission_sources_and_sinks,
            "data_source": plan.data_source,
            "measurement_methods_procedures_accuracy_calibration": (
                plan.measurement_methods_procedures_accuracy_calibration
            ),
            "quality_assessment_or_quality_control_procedures": (
                plan.quality_assessment_or_quality_control_procedures
            ),
            "responsibility_for_collection_and_archiving": (
                plan.responsibility_for_collection_and_archiving
            ),
        }
    )


def parcel_payload(parcel) -> dict:
    return _clean(
        {
            "multipolygon_coordinates": json.loads(parcel.geometry.geojson),
            "coordinate_reference_system": "EPSG:4326",
            "iacs_codes": _csv(parcel.iacs_codes),
            "lpis_codes": _csv(parcel.lpis_codes),
        }
    )


def parcel_owner_verification_payload(verification) -> dict:
    return _clean(
        {
            "parcel_id": verification.parcel.dcr_id,
            "status_code": verification.status_code,
            "status_message": verification.status_message,
            "parcel_owner_legal_name": verification.parcel_owner_legal_name,
            "authority": verification.authority,
            "documents": documents_payload(
                verification.parcel.documents.filter(is_current=True, kind="ownership_proof")
            ),
        }
    )


def activity_parcel_payload(link) -> dict:
    return _clean(
        {
            "parcel_id": link.parcel.dcr_id,
            "activity_id": link.project.dcr_id,
            "status_code": link.status_code,
            "status_message": link.status_message,
            "amount": link.amount,
            "documents": documents_payload(
                list(link.parcel.documents.filter(is_current=True))
                + list(link.project.documents.filter(is_current=True))
            ),
        }
    )


def parcel_activity_payload(link) -> dict:
    """New-style parcel/activity link (no verification state; the certifier owns that)."""
    return _clean(
        {
            "parcel_id": link.parcel.dcr_id,
            "activity_id": link.project.dcr_id,
            "activity_type": link.project.type,
        }
    )
