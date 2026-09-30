"""
Project lifecycle and the explicit push to DCR.

Rule: everything is authored and stored locally; nothing reaches DCR until the user
submits. `submit_project` then creates (or updates, if already synced) the DCR
entities in dependency order, on behalf of the user (their DirectLogin token).
"""

import logging

from django.utils import timezone

from apps.dcr.client import DCRClient, get_client
from apps.dcr.exceptions import DCRError

from . import dcr_mapping
from .models import Project

logger = logging.getLogger(__name__)


class InvalidTransition(Exception):
    pass


class NotReadyForSubmission(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class SubmitError(Exception):
    """A DCR call failed; `step` names the entity that failed."""

    def __init__(self, step: str, detail: str, status_code: int | None = None):
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail
        self.status_code = status_code


def mark_ready(project: Project) -> Project:
    if project.status != Project.Status.DRAFT:
        raise InvalidTransition(f"Cannot mark a {project.status} project as ready")
    project.status = Project.Status.READY
    project.save(update_fields=["status", "updated_at"])
    return project


def reopen(project: Project) -> Project:
    if project.status != Project.Status.READY:
        raise InvalidTransition(f"Cannot reopen a {project.status} project")
    project.status = Project.Status.DRAFT
    project.save(update_fields=["status", "updated_at"])
    return project


def readiness(project: Project) -> dict:
    """
    Checklist for submission. `error` checks block submit; `warning` checks are advice
    the frontend can show (missing plan, parcels without documents, ...).
    """
    checks: list[dict] = []

    def check(code: str, ok: bool, message: str, level: str = "error") -> None:
        checks.append({"code": code, "ok": bool(ok), "level": level, "message": message})

    operator = project.operator
    check("operator", operator is not None, "Set the operator")
    if operator is not None:
        for field in ("legal_name", "email", "country_code"):
            check(f"operator.{field}", bool(getattr(operator, field)), f"Fill operator {field}")
    for field in ("name", "start_date", "end_date", "type"):
        check(field, bool(getattr(project, field)), f"Fill {field}")
    if project.start_date and project.end_date:
        check(
            "dates",
            project.end_date >= project.start_date,
            "end_date must not be before start_date",
        )
    check("geometry", project.geometry is not None, "Draw the activity boundary", "warning")
    check(
        "certification_scheme",
        bool(project.certification_scheme_name),
        "Choose a certification scheme",
        "warning",
    )
    check(
        "description",
        bool(project.summary or project.description),
        "Add a summary or description",
        "warning",
    )

    plan = getattr(project, "plan", None)
    check("activity_plan", plan is not None, "Fill the activity plan", "warning")
    if plan is not None:
        for field in (
            "methodology_quantification_baseline",
            "expected_total_carbon_removals",
            "expected_total_soil_emissions",
            "expected_total_ghg_emissions_associated",
            "expected_net_benefit",
        ):
            check(
                f"activity_plan.{field}",
                getattr(plan, field) not in (None, ""),
                f"Fill plan {field}",
            )

    monitoring = getattr(project, "monitoring_plan", None)
    check("monitoring_plan", monitoring is not None, "Fill the monitoring plan", "warning")
    if monitoring is not None:
        for field in (
            "monitored_data_parameters",
            "monitoring_frequency",
            "measurement_methods_procedures_accuracy_calibration",
            "quality_assessment_or_quality_control_procedures",
            "responsibility_for_collection_and_archiving",
        ):
            check(
                f"monitoring_plan.{field}",
                getattr(monitoring, field) not in (None, "", []),
                f"Fill monitoring plan {field}",
            )

    links = list(project.parcel_links.select_related("parcel", "parcel__owner_verification"))
    check("parcels", len(links) > 0, "Link at least one parcel", "warning")
    for link in links:
        parcel = link.parcel
        label = str(parcel)
        check(
            f"parcel.{parcel.pk}.owner_verification",
            hasattr(parcel, "owner_verification"),
            f"Parcel {label}: fill the owner verification",
            "warning",
        )
        check(
            f"parcel.{parcel.pk}.ownership_proof",
            parcel.documents.filter(is_current=True, kind="ownership_proof").exists(),
            f"Parcel {label}: upload proof of ownership",
            "warning",
        )
        if project.operator_id and parcel.operator_id != project.operator_id:
            check(
                f"parcel.{parcel.pk}.operator", False, f"Parcel {label} belongs to another operator"
            )

    errors = [c["message"] for c in checks if c["level"] == "error" and not c["ok"]]
    warnings = [c["message"] for c in checks if c["level"] == "warning" and not c["ok"]]
    return {
        "ready": not errors,
        "editable": project.is_editable,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }


def submission_problems(project: Project) -> list[str]:
    return readiness(project)["errors"]


def submit_project(project: Project, dcr_token: str | None, user=None) -> Project:
    if not project.is_editable:
        raise InvalidTransition(f"Cannot submit a {project.status} project")
    problems = submission_problems(project)
    if problems:
        raise NotReadyForSubmission(problems)
    if not dcr_token:
        raise SubmitError("auth", "A DCR DirectLogin token is required to submit")

    client = get_client()
    operator = project.operator

    _sync(client, dcr_token, "operator", operator, dcr_mapping.operator_payload(operator))

    if user is not None and user.dcr_user_id:
        membership = operator.memberships.filter(user=user).first()
        if membership and not membership.dcr_id:
            _sync(
                client,
                dcr_token,
                "user_operator_relationship",
                membership,
                dcr_mapping.membership_payload(membership),
            )

    _sync(client, dcr_token, "activity", project, dcr_mapping.activity_payload(project))

    plan = getattr(project, "plan", None)
    if plan is not None:
        _sync(client, dcr_token, "activity_plan", plan, dcr_mapping.activity_plan_payload(plan))
        activity_fields = client.entity_fields("activity")
        if activity_fields is None or "activity_plan_id" in activity_fields:
            # older DCR schema: the activity carries a back-reference to its plan
            _sync(client, dcr_token, "activity", project, dcr_mapping.activity_payload(project))

    # Parcels, then the parcel/activity link. Owner verifications are written by the
    # certifier or the POV component (operator roles only allow reading them), so they
    # are never pushed from here; dcr-status mirrors them back.
    link_entity = _link_entity(client)
    for link in project.parcel_links.select_related("parcel"):
        parcel = link.parcel
        _sync(client, dcr_token, "parcel", parcel, dcr_mapping.parcel_payload(parcel))
        if link_entity == "parcel_activity":
            _sync(client, dcr_token, link_entity, link, dcr_mapping.parcel_activity_payload(link))
        elif link_entity == "activity_parcel_verification":
            _sync(client, dcr_token, link_entity, link, dcr_mapping.activity_parcel_payload(link))
        else:
            logger.warning(
                "DCR declares no parcel/activity link entity; parcel %s left unlinked", parcel.pk
            )

    monitoring_plan = getattr(project, "monitoring_plan", None)
    if monitoring_plan is not None:
        _sync(
            client,
            dcr_token,
            "monitoring_plan",
            monitoring_plan,
            dcr_mapping.monitoring_plan_payload(monitoring_plan),
        )

    project.status = Project.Status.SUBMITTED
    project.submitted_at = timezone.now()
    project.save(update_fields=["status", "submitted_at", "updated_at"])
    logger.info("project %s submitted to DCR as activity %s", project.pk, project.dcr_id)
    return project


def _link_entity(client: DCRClient) -> str | None:
    """
    The DCR entity that links a parcel to an activity: `parcel_activity` (current field
    sheet) once DCR declares it, otherwise the legacy `activity_parcel_verification`.
    """
    for entity in ("parcel_activity", "activity_parcel_verification"):
        exists = client.entity_exists(entity)
        if exists or (exists is None and entity == "activity_parcel_verification"):
            return entity
    return None


ID_PREFIXES = {
    "parcel_activity": "pa",
    "operator": "op",
    "user_operator_relationship": "uor",
    "activity": "act",
    "activity_plan": "ap",
    "monitoring_plan": "mp",
    "parcel": "parcel",
    "parcel_owner_verification": "pov",
    "activity_parcel_verification": "apv",
}


def _sync(client: DCRClient, token: str, entity: str, obj, payload: dict) -> dict:
    """
    Create the entity on DCR, or update it when it is already registered there.

    DCR expects the client to supply ids, so one is generated before the first create.
    The payload is trimmed to the properties DCR currently declares for the entity
    (live resource docs), so schema changes on the DCR side never break the call.
    """
    id_key = f"{entity}_id"
    obj.ensure_dcr_id(ID_PREFIXES.get(entity, entity))
    fields = client.entity_fields(entity)
    if fields is not None:
        payload = {k: v for k, v in payload.items() if k in fields}
    payload[id_key] = obj.dcr_id
    try:
        if obj.dcr_sync_status == obj.SyncStatus.SYNCED:
            response = client.update_entity(entity, obj.dcr_id, payload, token)
        else:
            response = client.create_entity(entity, payload, token)
            returned = client.entity_id_from(response, entity)
            if returned and returned != obj.dcr_id:
                obj.dcr_id = returned
    except DCRError as exc:
        obj.dcr_sync_status = obj.SyncStatus.FAILED
        obj.dcr_response = {
            "step": entity,
            "error": str(exc),
            "status_code": exc.status_code,
            "payload": exc.payload,
        }
        obj.save(update_fields=["dcr_id", "dcr_sync_status", "dcr_response"])
        logger.warning("DCR sync of %s failed for %r: %s", entity, obj, exc)
        raise SubmitError(entity, str(exc), exc.status_code) from exc

    obj.dcr_sync_status = obj.SyncStatus.SYNCED
    obj.dcr_synced_at = timezone.now()
    obj.dcr_response = response if isinstance(response, dict) else {"raw": response}
    obj.save(update_fields=["dcr_id", "dcr_sync_status", "dcr_synced_at", "dcr_response"])
    return response


# --------------------------------------------------------------------------- DCR status

VERIFICATION_TO_STATUS = {
    "verified": Project.Status.ACCEPTED,
    "failed": Project.Status.REJECTED,
    "in_progress": Project.Status.SUBMITTED,
}


def dcr_status_snapshot(project: Project) -> dict:
    """What we last learned from DCR, without calling it."""
    links = project.parcel_links.select_related("parcel", "parcel__owner_verification")
    parcels = []
    for link in links:
        verification = getattr(link.parcel, "owner_verification", None)
        parcels.append(
            {
                "parcel": link.parcel_id,
                "parcel_dcr_id": link.parcel.dcr_id,
                "status_code": link.status_code,
                "status_message": link.status_message,
                "amount": link.amount,
                "owner_verification": (
                    {
                        "status_code": verification.status_code,
                        "status_message": verification.status_message,
                        "authority": verification.authority,
                    }
                    if verification
                    else None
                ),
            }
        )
    return {
        "status": project.status,
        "dcr_id": project.dcr_id,
        "verification": project.dcr_verification or None,
        "parcels": parcels,
        "checked_at": project.dcr_status_checked_at,
    }


def refresh_dcr_status(project: Project, dcr_token: str | None) -> dict:
    """
    Pull the verification outcome from DCR and mirror it locally:
    activity_verification -> project.status, activity_parcel_verification -> parcel links,
    parcel_owner_verification -> parcel owner verifications.
    """
    if not project.dcr_id:
        raise InvalidTransition("Project has not been submitted to DCR yet")
    if not dcr_token:
        raise SubmitError("auth", "A DCR DirectLogin token is required")
    client = get_client()

    def fetch(entity: str, **params) -> list[dict]:
        try:
            return client.list_entities(entity, dcr_token, params=params or None)
        except DCRError as exc:
            raise SubmitError(entity, str(exc), exc.status_code) from exc

    activity_rows = [
        r
        for r in fetch("activity_verification", activity_id=project.dcr_id)
        if r.get("activity_id") == project.dcr_id
    ]
    links = list(project.parcel_links.select_related("parcel", "parcel__owner_verification"))
    if activity_rows:
        project.dcr_verification = activity_rows[-1]
        # a verdict per parcel (activity_verification carries parcel_id) drives the links;
        # the project follows: any failed -> rejected, all verified -> accepted
        statuses = {r.get("status_code") for r in activity_rows}
        if "failed" in statuses:
            new_status = Project.Status.REJECTED
        elif statuses == {"verified"}:
            new_status = Project.Status.ACCEPTED
        else:
            new_status = Project.Status.SUBMITTED
        if project.status not in Project.EDITABLE_STATUSES:
            project.status = new_status
        by_parcel = {r.get("parcel_id"): r for r in activity_rows if r.get("parcel_id")}
        for link in links:
            row = by_parcel.get(link.parcel.dcr_id)
            if row:
                link.status_code = row.get("status_code") or link.status_code
                link.status_message = row.get("status_message") or ""
                link.dcr_response = row
                link.save()

    if links:
        legacy = client.entity_exists("activity_parcel_verification")
        if legacy or legacy is None:
            legacy_rows = {
                r.get("parcel_id"): r
                for r in fetch("activity_parcel_verification", activity_id=project.dcr_id)
                if r.get("activity_id") == project.dcr_id
            }
            for link in links:
                row = legacy_rows.get(link.parcel.dcr_id)
                if row:
                    link.status_code = row.get("status_code") or link.status_code
                    link.status_message = row.get("status_message") or ""
                    link.amount = row.get("amount", link.amount)
                    link.dcr_response = row
                    link.save()
        parcel_ids = {link.parcel.dcr_id for link in links if link.parcel.dcr_id}
        owner_by_parcel = {
            r.get("parcel_id"): r
            for r in fetch("parcel_owner_verification")
            if r.get("parcel_id") in parcel_ids
        }
        for link in links:
            owner_row = owner_by_parcel.get(link.parcel.dcr_id)
            if owner_row:
                _mirror_owner_verification(link.parcel, owner_row)

    project.dcr_status_checked_at = timezone.now()
    project.save(
        update_fields=["status", "dcr_verification", "dcr_status_checked_at", "updated_at"]
    )
    return dcr_status_snapshot(project)


def _mirror_owner_verification(parcel, row: dict) -> None:
    from apps.parcels.models import ParcelOwnerVerification

    verification = getattr(parcel, "owner_verification", None)
    if verification is None:
        verification = ParcelOwnerVerification(parcel=parcel)
    verification.status_code = row.get("status_code") or verification.status_code
    verification.status_message = row.get("status_message") or ""
    verification.parcel_owner_legal_name = row.get("parcel_owner_legal_name") or ""
    verification.authority = row.get("authority") or verification.authority
    verification.dcr_id = verification.dcr_id or str(row.get("parcel_owner_verification_id") or "")
    verification.dcr_sync_status = verification.SyncStatus.SYNCED
    verification.dcr_synced_at = timezone.now()
    verification.dcr_response = row
    verification.save()
