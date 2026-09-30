"""
DCR entitlements the operator platform needs on behalf of a user.

DCR grants roles per user (see /users/current -> entitlements). Writes to a dynamic
entity require CanCreateDynamicEntity_System<entity> (and Update/Get/Delete variants).
"""

SUBMIT_ENTITIES = [
    "operator",
    "user_operator_relationship",
    "activity",
    "activity_plan",
    "monitoring_plan",
    "parcel",
    "parcel_owner_verification",
    "activity_parcel_verification",
]

STATUS_ENTITIES = [
    "activity_verification",
    "activity_parcel_verification",
    "parcel_owner_verification",
]

REFERENCE_ENTITIES = ["certification_scheme"]


def required_roles() -> dict[str, list[str]]:
    return {
        "submit": [f"CanCreateDynamicEntity_System{e}" for e in SUBMIT_ENTITIES]
        + [f"CanUpdateDynamicEntity_System{e}" for e in ("activity", "operator")],
        "status": [f"CanGetDynamicEntity_System{e}" for e in STATUS_ENTITIES],
        "reference": [f"CanGetDynamicEntity_System{e}" for e in REFERENCE_ENTITIES],
    }


def _rows(profile: dict) -> list[dict]:
    entitlements = (profile or {}).get("entitlements") or {}
    return entitlements.get("list", []) if isinstance(entitlements, dict) else list(entitlements)


def role_names(profile: dict, system_only: bool = True) -> list[str]:
    """
    Role names the user holds. OGCR entities are *system-level* dynamic entities, so only
    entitlements without a bank_id count; bank-scoped grants (e.g. bank "OGCR") are ignored
    by DCR for these endpoints even though they appear in the profile.
    """
    return sorted(
        {
            row.get("role_name", "")
            for row in _rows(profile)
            if row.get("role_name") and (not system_only or not row.get("bank_id"))
        }
    )


def bank_scoped_roles(profile: dict) -> dict[str, list[str]]:
    """Roles granted per bank, reported so a mis-scoped grant is visible."""
    out: dict[str, set[str]] = {}
    for row in _rows(profile):
        if row.get("bank_id") and row.get("role_name"):
            out.setdefault(row["bank_id"], set()).add(row["role_name"])
    return {bank: sorted(names) for bank, names in out.items()}


def capabilities(profile: dict) -> dict:
    """What the user can currently do on DCR, and which roles are missing for the rest."""
    held = set(role_names(profile))
    result = {}
    for capability, roles in required_roles().items():
        missing = [r for r in roles if r not in held]
        result[capability] = {"allowed": not missing, "missing_roles": missing}
    return result
