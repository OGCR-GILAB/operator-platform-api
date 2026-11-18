"""
Read-only reference data fetched from DCR (e.g. certification schemes).

Reads are allowed at any time; only writes wait for an explicit user submission.
Results are cached briefly so the frontend can call them freely. While DCR does not
expose the data (missing roles, empty registry) a built-in sample list keeps the
frontend usable; every sample row carries `source: "sample"`.
"""

import json
import logging

from django.conf import settings
from django.core.cache import cache

from .client import get_client
from .exceptions import DCRError

logger = logging.getLogger(__name__)


def _scheme(scheme_id, name, decision, methodology_id, version, description):
    return {
        "certification_scheme_id": scheme_id,
        "name": name,
        "commission_decision_reference": decision,
        "certification_methodology": json.dumps(methodology_id),
        "scheme_version_number": version,
        "description": description,
        "source": "sample",
    }


SAMPLE_CERTIFICATION_SCHEMES = [
    _scheme(
        "cs_sample_crcf_carbon_farming",
        "EU CRCF Carbon Farming Scheme",
        "Commission Decision C(2026) 1234",
        {"id": "crcf_agri_mineral_soils", "version": "1.0"},
        "v1.3",
        "Carbon farming on mineral soils under Regulation (EU) 2024/3012.",
    ),
    _scheme(
        "cs_sample_crcf_permanent_removal",
        "EU CRCF Permanent Carbon Removal Scheme",
        "Commission Decision C(2026) 1235",
        {"id": "crcf_biochar", "version": "1.0"},
        "v1.1",
        "Permanent removals (biochar, BECCS, DACCS) with storage monitoring obligations.",
    ),
    _scheme(
        "cs_sample_crcf_storage_products",
        "EU CRCF Carbon Storage in Products Scheme",
        "Commission Decision C(2026) 1236",
        {"id": "crcf_wood_construction", "version": "1.0"},
        "v1.0",
        "Carbon storage in long-lived wood-based construction products.",
    ),
    _scheme(
        "cs_sample_label_bas_carbone",
        "Label Bas-Carbone (France)",
        "Arrete du 28 novembre 2018 (FR)",
        {"id": "lbc_grandes_cultures", "version": "2.0"},
        "v2.0",
        "French national low-carbon label; methods for arable crops, orchards and hedgerows.",
    ),
    _scheme(
        "cs_sample_verra_vm0042",
        "Verra VCS - Improved Agricultural Land Management",
        "",
        {"id": "VM0042", "version": "2.1"},
        "v2.1",
        "VCS methodology VM0042 for soil carbon and emission reductions from land management.",
    ),
    _scheme(
        "cs_sample_gold_standard_soc",
        "Gold Standard - Soil Organic Carbon Framework",
        "",
        {"id": "GS_SOC_Framework", "version": "1.2"},
        "v1.2",
        "Gold Standard framework methodology for soil organic carbon activities.",
    ),
]


def _dcr_scheme(row: dict) -> dict:
    """
    A DCR certification_scheme row in the shape the API serves. DCR renamed `name` to
    `legal_name` (Nov 2026); `name` stays in the response, filled from either.
    """
    row = dict(row)
    row.setdefault("source", "dcr")
    row["name"] = row.get("name") or row.get("legal_name") or row.get("certification_scheme_id", "")
    return row


def certification_schemes(token: str) -> list[dict]:
    key = "dcr:reference:certification_scheme"
    cached = cache.get(key)
    if cached is not None:
        return cached
    schemes: list[dict] = []
    client = get_client()
    try:
        try:
            # published without authentication on the bank-level model
            schemes = client.list_public_entities("certification_scheme")
        except DCRError:
            schemes = client.list_entities("certification_scheme", token)
        schemes = [_dcr_scheme(row) for row in schemes if isinstance(row, dict)]
    except DCRError as exc:
        if not settings.DCR["REFERENCE_SAMPLE_FALLBACK"]:
            raise
        logger.warning("certification_scheme not available from DCR (%s); serving samples", exc)
    if not schemes and settings.DCR["REFERENCE_SAMPLE_FALLBACK"]:
        schemes = [dict(s) for s in SAMPLE_CERTIFICATION_SCHEMES]
    cache.set(key, schemes, settings.DCR["REFERENCE_CACHE_SECONDS"])
    return schemes


# ------------------------------------------------------------------ vocabularies
# Bank-level vocabularies DCR publishes without authentication
# (/obp/dynamic-entity/banks/<bank>/public/<entity>), mapped to {"value", "label", ...}.

# DCR renames plural entities to singular ones (Nov 2026); where a rename is pending, the
# candidates are tried in order: the expected singular name first, the current name last.
VOCABULARIES = {
    # name: (DCR entity, id property, label property, {extra output key: DCR property})
    "countries": ("country", "country_id", "country_name", {}),
    "sustainable_development_goals": (
        "sustainable_development_goal",
        "sustainable_development_goal_id",
        "sustainable_development_goal_name",
        {"icon": "sustainable_development_goal_link"},
    ),
    "technologies_practices_processes": (
        ("technology_practice_process", "technologies_practices_processes"),
        ("technology_practice_process_id", "technologies_practices_processes_id"),
        ("practice_name", "technology_practice_process_name"),
        {},
    ),
    "co_benefits": ("co_benefit", "co_benefit_id", "co_benefit_name", {}),
}


def _names(value) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value)


def vocabulary(name: str) -> list[dict] | None:
    """A DCR vocabulary sorted by label (cached); None when DCR cannot serve it."""
    entities, id_keys, label_keys, extra = (
        _names(part) if i < 3 else part for i, part in enumerate(VOCABULARIES[name])
    )
    key = f"dcr:vocabulary:{name}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    rows = None
    for entity in entities:
        try:
            rows = get_client().list_public_entities(entity)
            break
        except DCRError as exc:
            if exc.status_code != 404:
                logger.warning("vocabulary %s not available from DCR (%s)", entity, exc)
                return None
    if rows is None:
        logger.warning("vocabulary %s not found on DCR under %s", name, ", ".join(entities))
        return None
    items = []
    for row in rows:
        id_key = next((k for k in id_keys if row.get(k)), id_keys[0])
        label_key = next((k for k in label_keys if row.get(k)), label_keys[0])
        value = row.get(id_key)
        if not value:
            continue
        item = {"value": value, "label": row.get(label_key) or value}
        item.update({out: row.get(prop) or "" for out, prop in extra.items()})
        items.append(item)
    items.sort(key=lambda item: item["label"].casefold())
    cache.set(key, items, settings.DCR["REFERENCE_CACHE_SECONDS"])
    return items


# --------------------------------------------------------------- password policy


def password_policy() -> dict | None:
    """DCR's password policies (GET public/password-config, cached); None when unavailable."""
    key = "dcr:password_config"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        policy = get_client().password_config()
    except DCRError as exc:
        logger.warning("password policy not available from DCR (%s)", exc)
        return None
    if not isinstance(policy, dict) or not policy.get("policies"):
        return None
    cache.set(key, policy, 3600)
    return policy


def password_satisfies(password: str, policy: dict) -> bool:
    """True when the password matches at least one of the policies (DCR's own rule)."""
    import re

    for rule in policy.get("policies", []):
        pattern = rule.get("regex")
        if pattern:
            try:
                if re.fullmatch(pattern, password):
                    return True
                continue
            except re.error:
                pass
        low, high = rule.get("min_length") or 0, rule.get("max_length") or 10_000
        classes = rule.get("required_character_classes", [])
        if low <= len(password) <= high and all(
            re.search(c.get("regex", ""), password) for c in classes
        ):
            return True
    return False
