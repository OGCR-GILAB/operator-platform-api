"""
DCR entitlements the operator platform needs on behalf of a user.

DCR grants roles per user (see /users/current -> entitlements). Two models coexist:
  * system-level dynamic entities: role `Can<Action>DynamicEntity_System<entity>`, no bank_id
  * bank-level dynamic entities (current model): role `Can<Action>DynamicEntityRecord_<entity>`
    granted at the platform's bank (DCR_BANK_ID, default "ogcr")
A requirement is met by either form.
"""

from django.conf import settings

# (action, entity) pairs; a tuple of entities means "any one of them"
SUBMIT = [
    ("Create", "operator"),
    ("Update", "operator"),
    ("Create", "user_operator_relationship"),
    ("Create", "activity"),
    ("Update", "activity"),
    ("Create", "activity_plan"),
    ("Create", "monitoring_plan"),
    # parcels and their links to activities are written by the certification platform
]
STATUS = [
    ("Get", "activity_verification"),
    ("Get", "parcel_owner_verification"),
]
REFERENCE = [("Get", "certification_scheme")]


def _bank() -> str:
    return settings.DCR.get("BANK_ID") or "ogcr"


def _rows(profile: dict) -> list[dict]:
    entitlements = (profile or {}).get("entitlements") or {}
    return entitlements.get("list", []) if isinstance(entitlements, dict) else list(entitlements)


def _held(profile: dict) -> set[tuple[str, str]]:
    """(role_name, bank_id) pairs; bank_id '' for system-level grants."""
    return {
        (r.get("role_name", ""), r.get("bank_id") or "")
        for r in _rows(profile)
        if r.get("role_name")
    }


def _satisfied(held: set[tuple[str, str]], action: str, entity: str) -> bool:
    return (f"Can{action}DynamicEntity_System{entity}", "") in held or (
        f"Can{action}DynamicEntityRecord_{entity}",
        _bank(),
    ) in held


def _label(action: str, entities) -> str:
    entity = entities if isinstance(entities, str) else entities[0]
    return f"Can{action}DynamicEntityRecord_{entity}"


def required_roles() -> dict[str, list[str]]:
    return {
        name: [_label(a, e) for a, e in reqs]
        for name, reqs in (("submit", SUBMIT), ("status", STATUS), ("reference", REFERENCE))
    }


def role_names(profile: dict, system_only: bool = True) -> list[str]:
    """
    Role names that count for the OGCR entities: system-level grants plus grants at the
    platform's bank. Grants at other banks are ignored (see bank_scoped_roles).
    """
    bank = _bank()
    return sorted({name for name, b in _held(profile) if not system_only or b in ("", bank)})


def bank_scoped_roles(profile: dict) -> dict[str, list[str]]:
    """Roles granted per bank, reported so a mis-scoped grant is visible."""
    out: dict[str, set[str]] = {}
    for name, bank in _held(profile):
        if bank:
            out.setdefault(bank, set()).add(name)
    return {bank: sorted(names) for bank, names in out.items()}


def capabilities(profile: dict) -> dict:
    """What the user can currently do on DCR, and which roles are missing for the rest."""
    held = _held(profile)
    result = {}
    for name, reqs in (("submit", SUBMIT), ("status", STATUS), ("reference", REFERENCE)):
        missing = []
        for action, entities in reqs:
            options = (entities,) if isinstance(entities, str) else entities
            if not any(_satisfied(held, action, e) for e in options):
                missing.append(_label(action, entities))
        result[name] = {"allowed": not missing, "missing_roles": missing}
    return result


# ----------------------------------------------------------------- live check
# DCR v7.0.0 answers "does the caller hold role X at bank B?" directly
# (GET /banks/<bank>/my/roles/<role>), counting roles held by the user or by the consumer.
# This is authoritative where the /users/current entitlement list is not (consumer roles,
# roles granted after login), so submit checks it before writing anything.


def _role_status(client, token: str, role: str) -> dict:
    from .exceptions import DCRAuthError, DCRError

    try:
        data = client.my_role(token, role, _bank())
    except DCRAuthError:
        raise
    except DCRError as exc:
        return {"role": role, "held": None, "held_by": {}, "error": str(exc)}
    if not isinstance(data, dict) or "held_by" not in data:
        return {"role": role, "held": None, "held_by": {}, "error": "unexpected response"}
    held_by = data.get("held_by") or {}
    return {
        "role": role,
        "held": bool(held_by.get("user") or held_by.get("consumer")),
        "held_by": {"user": bool(held_by.get("user")), "consumer": bool(held_by.get("consumer"))},
        "error": "",
    }


def live_permissions(token: str, client=None, use_cache: bool = True) -> dict | None:
    """
    Check every bank-level role the platform needs against DCR, in parallel.
    Returns {"bank_id", "roles": [...], "capabilities": {...}} or None when DCR cannot
    answer (e.g. an API version without the endpoint), so callers fall back to the profile.
    """
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    from django.core.cache import cache
    from django.utils import timezone

    from .client import get_client

    key = "dcr:permissions:" + hashlib.sha256(f"{_bank()}:{token}".encode()).hexdigest()
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached

    client = client or get_client()
    wanted: list[str] = []
    for _name, reqs in (("submit", SUBMIT), ("status", STATUS), ("reference", REFERENCE)):
        for action, entities in reqs:
            for entity in (entities,) if isinstance(entities, str) else entities:
                role = f"Can{action}DynamicEntityRecord_{entity}"
                if role not in wanted:
                    wanted.append(role)
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda role: _role_status(client, token, role), wanted))
    if all(row["held"] is None for row in rows):
        return None

    state = {row["role"]: row["held"] for row in rows}
    capabilities_ = {}
    for name, reqs in (("submit", SUBMIT), ("status", STATUS), ("reference", REFERENCE)):
        missing, unknown = [], []
        for action, entities in reqs:
            options = [
                state.get(f"Can{action}DynamicEntityRecord_{e}")
                for e in ((entities,) if isinstance(entities, str) else entities)
            ]
            if any(options):
                continue
            # a role DCR could not report on is "unknown", never counted as missing
            (unknown if None in options else missing).append(_label(action, entities))
        capabilities_[name] = {
            "allowed": not missing and not unknown,
            "missing_roles": missing,
            "unknown_roles": unknown,
        }
    result = {
        "bank_id": _bank(),
        "checked_at": timezone.now().isoformat(),
        "roles": rows,
        "capabilities": capabilities_,
    }
    cache.set(key, result, 300)
    return result
