"""
Project lifecycle and the explicit push to DCR.

Rule: everything is authored and stored locally; nothing reaches DCR until the user
submits. `submit_project` then creates (or updates, if already synced) the DCR
entities in dependency order, on behalf of the user (their DirectLogin token).
"""

import logging

from django.utils import timezone

from apps.core import reference
from apps.core.models import new_dcr_id
from apps.dcr.client import DCRClient, get_client
from apps.dcr.exceptions import DCRError
from apps.dcr.roles import live_permissions

from . import dcr_links, dcr_mapping
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


class MissingDCRRoles(Exception):
    """The user's DCR account lacks roles needed to submit; nothing was written."""

    def __init__(self, missing_roles: list[str], bank_id: str):
        super().__init__(", ".join(missing_roles))
        self.missing_roles = missing_roles
        self.bank_id = bank_id


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


def readiness(project: Project, dcr_token: str | None = None) -> dict:
    """
    Checklist for submission. `error` checks block submit; `warning` checks are advice
    the frontend can show (missing plan, parcels without documents, ...). With a DCR
    token, a `dcr.roles` warning reports roles DCR says the account lacks for submit.
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
    if project.geometry is None and project.parcel_links.exists():
        check(
            "geometry",
            False,
            "No activity boundary drawn: the outline of its parcels will be sent to DCR",
            "warning",
        )
    else:
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
        # the owner verification itself is the certifier's (certification platform); the
        # operator only has to provide the proof it is based on
        check(
            f"parcel.{parcel.pk}.ownership_proof",
            parcel.documents.filter(is_current=True, kind="ownership_proof").exists(),
            f"Parcel {label}: upload proof of ownership",
            "warning",
        )
        owner = getattr(parcel, "owner", None)
        check(
            f"parcel.{parcel.pk}.owner",
            owner is not None and owner.is_complete,
            f"Parcel {label}: declare its owner (name, type, country and the deed or extract)",
            "warning",
        )
        check(
            f"parcel.{parcel.pk}.cadastral_reference",
            bool(parcel.cadastral_reference),
            f"Parcel {label}: add its cadastral reference (required by DCR)",
        )
        if project.operator_id and parcel.operator_id != project.operator_id:
            check(
                f"parcel.{parcel.pk}.operator", False, f"Parcel {label} belongs to another operator"
            )

    for field, values in dcr_links.unlinked_values(project).items():
        check(
            f"{field}.vocabulary",
            False,
            f"Not in the DCR list, sent as text only ({field}): " + ", ".join(values),
            "warning",
        )

    if dcr_token:
        permissions = None
        try:
            permissions = live_permissions(dcr_token, client=get_client())
        except DCRError as exc:
            logger.warning("DCR role check skipped in readiness: %s", exc)
        if permissions is not None:
            missing = permissions["capabilities"]["submit"]["missing_roles"]
            check(
                "dcr.roles",
                not missing,
                "Your DCR account is missing roles needed to submit: " + ", ".join(missing)
                if missing
                else "Your DCR account has the roles needed to submit",
                "warning",
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
    # fail before the first write when DCR says a needed role is missing
    permissions = live_permissions(dcr_token, client=client)
    if permissions is not None:
        missing = permissions["capabilities"]["submit"]["missing_roles"]
        if missing:
            raise MissingDCRRoles(missing, permissions["bank_id"])
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
    if getattr(project, "_dcr_recreated", False):
        # the activity was registered again from scratch: its earlier link records are gone
        project.dcr_links = {}
        project.save(update_fields=["dcr_links", "updated_at"])

    plan = getattr(project, "plan", None)
    if plan is not None:
        _stamp_submitted(plan)
        _sync(client, dcr_token, "activity_plan", plan, dcr_mapping.activity_plan_payload(plan))
        activity_fields = client.entity_fields("activity")
        if activity_fields is None or "activity_plan_id" in activity_fields:
            # older DCR schema: the activity carries a back-reference to its plan
            _sync(client, dcr_token, "activity", project, dcr_mapping.activity_payload(project))

    # Parcels and their links to the activity are registered on DCR by the certification
    # platform, from what our partner API serves (parcels linked to submitted activities,
    # with cadastral references and documents). The activity boundary above covers them.

    monitoring_plan = getattr(project, "monitoring_plan", None)
    if monitoring_plan is not None:
        _stamp_submitted(monitoring_plan)
        _sync(
            client,
            dcr_token,
            "monitoring_plan",
            monitoring_plan,
            dcr_mapping.monitoring_plan_payload(monitoring_plan),
        )

    links = list(project.parcel_links.select_related("parcel"))
    _forget_missing_parcels(client, dcr_token, links)
    # owners declared for parcels the certification platform has already registered
    sync_parcel_owners(client, dcr_token, links)

    # vocabulary links (practices, co-benefits, SDGs) and activity media
    try:
        dcr_links.sync_links(client, dcr_token, project)
    except dcr_links.LinkError as exc:
        raise SubmitError(exc.step, exc.detail, exc.status_code) from exc

    if project.status == Project.Status.CHANGES_REQUESTED:
        # this resubmit answers the open request; dcr-status must not reopen for it again
        project.dcr_amendment_answered = {
            "verification": project.dcr_verification or {},
            "audit_reports": _audit_report_ids(project),
        }
    project.status = Project.Status.SUBMITTED
    project.submitted_at = project.submitted_at or timezone.now()
    project.save(update_fields=["status", "submitted_at", "dcr_amendment_answered", "updated_at"])
    logger.info("project %s submitted to DCR as activity %s", project.pk, project.dcr_id)
    return project


def _stamp_submitted(plan) -> None:
    """Fix the submission date at the first submit, so a resubmit does not move it."""
    if plan.date_submitted is None:
        plan.date_submitted = timezone.localdate()
        plan.save(update_fields=["date_submitted"])


ID_PREFIXES = {
    "parcel_activity": "pa",
    "operator": "op",
    "user_operator_relationship": "uor",
    "activity": "act",
    "activity_plan": "ap",
    "monitoring_plan": "mp",
    "parcel": "parcel",
    "parcel_owner_verification": "pov",
    "parcel_owner": "po",
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
        recreate = False
        if obj.dcr_sync_status == obj.SyncStatus.SYNCED:
            try:
                response = client.update_entity(entity, obj.dcr_id, payload, token)
            except DCRError as exc:
                if exc.status_code != 404:
                    raise
                # the record is gone from DCR (e.g. a registry reset): register it again
                logger.warning("%s %s missing on DCR, creating it again", entity, obj.dcr_id)
                recreate = True
        obj._dcr_recreated = recreate
        if obj.dcr_sync_status != obj.SyncStatus.SYNCED or recreate:
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
    "denied": Project.Status.REJECTED,
    "draft": Project.Status.SUBMITTED,
    "in_progress": Project.Status.SUBMITTED,
    "pending": Project.Status.SUBMITTED,
    "under_review": Project.Status.SUBMITTED,
    "validated": Project.Status.SUBMITTED,  # validation passed, verification still to come
}


def dcr_status_snapshot(project: Project) -> dict:
    """What we last learned from DCR, without calling it."""
    links = project.parcel_links.select_related(
        "parcel", "parcel__owner_verification", "parcel__owner"
    )
    parcels = []
    for link in links:
        verification = getattr(link.parcel, "owner_verification", None)
        parcels.append(
            {
                "parcel": link.parcel_id,
                "parcel_dcr_id": link.parcel.dcr_id,
                "name": link.parcel.name,
                "cadastral_reference": link.parcel.cadastral_reference,
                # a verdict of its own: the certifier wrote a verification row for this parcel
                # (the link's sync response from parcel_activity carries no status)
                "has_verdict": "status_code" in (link.dcr_response or {}),
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
                "owner": _owner_row(getattr(link.parcel, "owner", None)),
            }
        )
    certificates = project.dcr_certificates or []
    return {
        "amendment": amendment_request(project),
        "status": project.status,
        "dcr_id": project.dcr_id,
        "verification": project.dcr_verification or None,
        "parcels": parcels,
        "certificate": next(
            (c for c in certificates if c.get("certification_status") in VALID_CERTIFICATE), None
        ),
        "certificates": certificates,
        "audit_reports": project.dcr_audit_reports or [],
        "checked_at": project.dcr_status_checked_at,
    }


def _owner_row(owner) -> dict | None:
    if owner is None:
        return None
    failed = owner.dcr_sync_status == owner.SyncStatus.FAILED
    return {
        "legal_name": owner.legal_name,
        "owner_type": owner.owner_type,
        "country_code": owner.country_code,
        "document": owner.document_id,
        "is_complete": owner.is_complete,
        "dcr_id": owner.dcr_id,
        "dcr_sync_status": owner.dcr_sync_status,
        "error": (owner.dcr_response or {}).get("error") if failed else None,
    }


def _exists(client: DCRClient, token: str, entity: str, record_id: str) -> bool:
    try:
        client.get_entity(entity, record_id, token)
        return True
    except DCRError as exc:
        if exc.status_code == 404:
            return False
        raise SubmitError(entity, str(exc), exc.status_code) from exc


def _forget_dcr_outcome(project: Project) -> dict:
    """
    The activity is gone from DCR (e.g. a registry reset): for the registry it was never
    submitted. Back to `ready` so the operator can submit it again (the same ids are reused);
    verdicts mirrored from the old record no longer apply.
    """
    logger.warning("activity %s of project %s is no longer on DCR", project.dcr_id, project.pk)
    project.status = Project.Status.READY
    project.dcr_verification = {}
    project.dcr_certificates = []
    project.dcr_audit_reports = []
    project.dcr_amendment_answered = {}
    project.dcr_status_checked_at = timezone.now()
    project.save()
    return dcr_status_snapshot(project)


def _forget_missing_parcels(client: DCRClient, token: str, links) -> None:
    """
    Parcels the certification platform registered may be gone from DCR (a registry reset).
    Such a parcel is local again: the certification platform registers it anew after this
    submit, our `parcel_owner` is created again once it has, and the owner verification and
    per-parcel verdicts mirrored from the old records no longer apply.
    """
    from apps.parcels.models import ParcelOwner, ParcelOwnerVerification

    for link in links:
        parcel = link.parcel
        if not parcel.dcr_id:
            continue
        try:
            if _exists(client, token, "parcel", parcel.dcr_id):
                continue
        except SubmitError as exc:
            logger.warning("could not check parcel %s on DCR: %s", parcel.dcr_id, exc)
            continue
        logger.warning("parcel %s of %s is no longer on DCR", parcel.dcr_id, parcel.pk)
        parcel.dcr_id = ""
        parcel.dcr_sync_status = parcel.SyncStatus.LOCAL
        parcel.dcr_synced_at = None
        parcel.dcr_response = {}
        parcel.save(update_fields=["dcr_id", "dcr_sync_status", "dcr_synced_at", "dcr_response"])
        ParcelOwner.objects.filter(parcel=parcel).update(
            dcr_sync_status=ParcelOwner.SyncStatus.LOCAL,
            dcr_synced_at=None,
            dcr_response={},
            supporting_document_dcr_id="",
        )
        ParcelOwnerVerification.objects.filter(parcel=parcel).delete()
        for other in parcel.project_links.all():
            other.status_code = other._meta.get_field("status_code").default
            other.status_message = ""
            other.amount = None
            other.dcr_response = {}
            other.save(update_fields=["status_code", "status_message", "amount", "dcr_response"])


def _audit_report_ids(project: Project) -> list[str]:
    return [r.get("audit_report_id") for r in project.dcr_audit_reports or [] if r]


def _unanswered(project: Project, verification: dict) -> bool:
    """
    Whether an amendment request still waits for the operator. DCR records carry no
    timestamps and a repeated request can look the same, but every request also writes an
    audit report, so a report the last resubmit did not know about means a new request.
    """
    answered = project.dcr_amendment_answered or {}
    if "verification" not in answered:  # nothing answered yet (or the older plain format)
        return verification != (answered or None)
    if verification != answered["verification"]:
        return True
    return bool(set(_audit_report_ids(project)) - set(answered.get("audit_reports", [])))


def amendment_request(project: Project) -> dict | None:
    """
    What the certifier asked to change, while the activity waits for the operator's
    resubmit: the stage (validation / verification) and the certifier's message
    (status_message of the verification, else the newest audit report's summary).
    """
    if project.status != Project.Status.CHANGES_REQUESTED:
        return None
    verification = project.dcr_verification or {}
    code = verification.get("status_code", "")
    reports = project.dcr_audit_reports or []
    message = verification.get("status_message") or (
        reports[0].get("audit_report_summary") if reports else ""
    )
    return {
        "stage": code.removesuffix("_amendment_required") or None,
        "status_code": code,
        "message": message or "",
    }


# certificate_of_compliance.certification_status values that make a certificate current
VALID_CERTIFICATE = ("certified", "active")


def _activity_records(client, token: str, entity: str, activity_id: str, newest: tuple):
    """
    Records of `entity` for one activity, newest first (by the `newest` date fields).
    Written by the certification platform and only read here, so a DCR error or a
    missing role returns None and the caller keeps what it mirrored last time.
    """
    try:
        rows = client.list_entities(entity, token, params={"activity_id": activity_id})
    except DCRError as exc:
        logger.warning("could not read %s for %s: %s", entity, activity_id, exc)
        return None
    rows = [r for r in rows if isinstance(r, dict) and r.get("activity_id") == activity_id]
    return sorted(rows, key=lambda r: tuple(r.get(f) or "" for f in newest), reverse=True)


def refresh_dcr_status(project: Project, dcr_token: str | None) -> dict:
    """
    Pull the verification outcome from DCR and mirror it locally:
    activity_verification -> project.status, activity_parcel_verification -> parcel links,
    parcel_owner_verification -> parcel owner verifications, certificate_of_compliance and
    audit_report -> the project's certificates and audit reports.
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

    if project.status not in (Project.Status.DRAFT, Project.Status.READY) and not _exists(
        client, dcr_token, "activity", project.dcr_id
    ):
        return _forget_dcr_outcome(project)

    activity_rows = [
        r
        for r in fetch("activity_verification", activity_id=project.dcr_id)
        if r.get("activity_id") == project.dcr_id
    ]
    links = list(project.parcel_links.select_related("parcel", "parcel__owner_verification"))
    _adopt_registry_parcels(client, dcr_token, project, links)
    sync_parcel_owners(client, dcr_token, links)
    certificates = _activity_records(
        client, dcr_token, "certificate_of_compliance", project.dcr_id, ("issue_date",)
    )
    if certificates is not None:
        project.dcr_certificates = certificates
    audit_reports = _activity_records(
        client, dcr_token, "audit_report", project.dcr_id, ("audit_report_issue_date", "audit_date")
    )
    if audit_reports is not None:
        project.dcr_audit_reports = audit_reports

    if activity_rows:
        project.dcr_verification = activity_rows[-1]
        # a verdict per parcel (activity_verification carries parcel_id) drives the links;
        # the project follows: any failed -> rejected, all verified -> accepted
        statuses = {r.get("status_code") for r in activity_rows}
        latest = activity_rows[-1]
        if latest.get("status_code") in reference.AMENDMENT_STATUSES and _unanswered(
            project, latest
        ):
            # an amendment request the operator has not answered with a resubmit yet
            new_status = Project.Status.CHANGES_REQUESTED
        elif statuses & set(reference.FAILED_VERDICTS):
            new_status = Project.Status.REJECTED
        elif statuses == {"verified"}:
            new_status = Project.Status.ACCEPTED
        else:
            new_status = Project.Status.SUBMITTED
        # local drafts stay drafts; everything after the first submit follows DCR
        if project.status not in (Project.Status.DRAFT, Project.Status.READY):
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
        sync_owner_verification_documents(client, dcr_token, links)

    project.dcr_status_checked_at = timezone.now()
    project.save(
        update_fields=[
            "status",
            "dcr_verification",
            "dcr_certificates",
            "dcr_audit_reports",
            "dcr_status_checked_at",
            "updated_at",
        ]
    )
    return dcr_status_snapshot(project)


def _adopt_registry_parcels(client: DCRClient, token: str, project: Project, links) -> None:
    """
    The certification platform registers our parcels on DCR under its own ids and links them
    to the activity. Match those records to ours by cadastral reference and remember the DCR
    id, so verdicts and owner verifications on them reach the right parcel here.
    """
    by_reference = {
        link.parcel.cadastral_reference: link for link in links if link.parcel.cadastral_reference
    }
    if not by_reference:
        return
    try:
        records = client.list_entities(
            "parcel_activity", token, params={"activity_id": project.dcr_id}
        )
    except DCRError as exc:
        logger.warning("could not read parcel links of %s: %s", project.dcr_id, exc)
        return
    for record in records:
        if record.get("activity_id") != project.dcr_id or not record.get("parcel_id"):
            continue
        try:
            parcel_record = client.get_entity("parcel", record["parcel_id"], token)
        except DCRError:
            continue
        link = by_reference.get((parcel_record or {}).get("cadastral_reference"))
        if link is None or link.parcel.dcr_id == record["parcel_id"]:
            continue
        parcel = link.parcel
        parcel.dcr_id = record["parcel_id"]
        parcel.dcr_sync_status = parcel.SyncStatus.SYNCED
        parcel.dcr_synced_at = timezone.now()
        parcel.dcr_response = parcel_record
        parcel.save(update_fields=["dcr_id", "dcr_sync_status", "dcr_synced_at", "dcr_response"])


def sync_parcel_owners(client: DCRClient, token: str, links) -> None:
    """
    Send the owners operators declared (`parcel_owner`, with its `supporting_document`) for
    parcels registered on DCR. Unchanged owners are skipped. A failure is recorded on the
    owner and logged; it never stops a submit or a status refresh.
    """
    from apps.parcels.models import ParcelOwner

    for link in links:
        parcel = link.parcel
        owner = ParcelOwner.objects.filter(parcel=parcel).select_related("document").first()
        if owner is None or not parcel.dcr_id or not owner.is_complete:
            continue
        document = _current_version(owner.document)
        try:
            document_id = _ensure_supporting_document(client, token, document)
        except DCRError as exc:
            owner.dcr_sync_status = owner.SyncStatus.FAILED
            owner.dcr_response = {"step": "supporting_document", "error": str(exc)}
            owner.save(update_fields=["dcr_sync_status", "dcr_response"])
            logger.warning("supporting document %s not registered: %s", document.pk, exc)
            continue
        payload = dcr_mapping.parcel_owner_payload(owner, parcel.dcr_id, document_id)
        sent = DCRClient.unwrap_entity(owner.dcr_response or {}, "parcel_owner") or {}
        if owner.is_synced and all(sent.get(k) == v for k, v in payload.items()):
            continue
        try:
            if owner.dcr_id and not owner.is_synced:
                if _exists(client, token, "parcel_owner", owner.dcr_id):
                    # an earlier attempt failed after the record was created: update it
                    owner.dcr_sync_status = owner.SyncStatus.SYNCED
            _sync(client, token, "parcel_owner", owner, payload)
        except SubmitError:
            continue  # recorded on the owner (failed) and shown in the status snapshot
        if owner.supporting_document_dcr_id != document_id:
            owner.supporting_document_dcr_id = document_id
            owner.save(update_fields=["supporting_document_dcr_id"])


POV_DOCUMENT = "parcel_owner_verification_supporting_document"


def sync_owner_verification_documents(client: DCRClient, token: str, links) -> None:
    """
    Attach the parcel's current documents to its owner verification on DCR.

    The certification platform links the documents it reads at submit to the
    `parcel_owner_verification` it creates; documents the operator adds later (a new deed, a
    new version, a cadastral extract) are linked here: a `supporting_document` plus a
    `parcel_owner_verification_supporting_document`. A document counts as linked when a linked
    supporting document points at the same download address, whatever id the certification
    platform gave it. A failure is logged and never stops the refresh.
    """
    from apps.parcels.models import ParcelOwnerVerification

    for link in links:
        parcel = link.parcel
        verification = ParcelOwnerVerification.objects.filter(parcel=parcel).first()
        if verification is None or not verification.dcr_id or not parcel.accepts_documents:
            continue
        documents = list(parcel.documents.filter(is_current=True).order_by("pk"))
        if not documents:
            continue
        try:
            linked_ids = {
                row.get("supporting_document_id")
                for row in client.list_entities(
                    POV_DOCUMENT,
                    token,
                    params={"parcel_owner_verification_id": verification.dcr_id},
                )
                if row.get("parcel_owner_verification_id") == verification.dcr_id
            }
            linked_urls = _document_urls(client, token, linked_ids)
            for document in documents:
                if dcr_mapping.supporting_document_url(document) in linked_urls:
                    continue
                document_id = _ensure_supporting_document(client, token, document)
                if document_id in linked_ids:
                    continue
                client.create_entity(
                    POV_DOCUMENT,
                    {
                        f"{POV_DOCUMENT}_id": new_dcr_id("sdlink"),
                        "supporting_document_id": document_id,
                        "parcel_owner_verification_id": verification.dcr_id,
                    },
                    token,
                )
                linked_ids.add(document_id)
        except DCRError as exc:
            logger.warning("documents of %s not linked to its verification: %s", parcel.pk, exc)


def _document_urls(client: DCRClient, token: str, document_ids) -> set[str]:
    """Download addresses of the given supporting documents (ones that cannot be read skipped)."""
    urls = set()
    for document_id in document_ids:
        if not document_id:
            continue
        try:
            record = client.get_entity("supporting_document", document_id, token) or {}
        except DCRError:
            continue
        if record.get("url"):
            urls.add(record["url"])
    return urls


def _current_version(document):
    from apps.documents.models import Document

    while True:
        newer = Document.objects.filter(supersedes=document).first()
        if newer is None:
            return document
        document = newer


def _ensure_supporting_document(client: DCRClient, token: str, document) -> str:
    """The DCR id of the document's `supporting_document`, registering it when missing."""
    payload = dcr_mapping.supporting_document_payload(document)
    record_id = payload["supporting_document_id"]
    try:
        client.get_entity("supporting_document", record_id, token)
        return record_id  # already there (also when the certification platform made it)
    except DCRError as exc:
        if exc.status_code != 404:
            raise
    fields = client.entity_fields("supporting_document")
    if fields is not None:
        payload = {k: v for k, v in payload.items() if k in fields}
    client.create_entity("supporting_document", payload, token)
    return record_id


def _mirror_owner_verification(parcel, row: dict) -> None:
    from apps.parcels.models import ParcelOwnerVerification

    verification = getattr(parcel, "owner_verification", None)
    if verification is None:
        verification = ParcelOwnerVerification(parcel=parcel)
    verification.status_code = row.get("status_code") or verification.status_code
    verification.status_message = row.get("status_message") or ""
    verification.parcel_owner_legal_name = row.get("parcel_owner_legal_name") or ""
    verification.authority = row.get("authority") or verification.authority
    # the record DCR has for the parcel now; an older one may have been removed and replaced
    verification.dcr_id = str(row.get("parcel_owner_verification_id") or verification.dcr_id)
    verification.dcr_sync_status = verification.SyncStatus.SYNCED
    verification.dcr_synced_at = timezone.now()
    verification.dcr_response = row
    verification.save()
