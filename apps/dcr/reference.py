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


def certification_schemes(token: str) -> list[dict]:
    key = "dcr:reference:certification_scheme"
    cached = cache.get(key)
    if cached is not None:
        return cached
    schemes: list[dict] = []
    try:
        schemes = get_client().list_entities("certification_scheme", token)
        for row in schemes:
            row.setdefault("source", "dcr")
    except DCRError as exc:
        if not settings.DCR["REFERENCE_SAMPLE_FALLBACK"]:
            raise
        logger.warning("certification_scheme not available from DCR (%s); serving samples", exc)
    if not schemes and settings.DCR["REFERENCE_SAMPLE_FALLBACK"]:
        schemes = [dict(s) for s in SAMPLE_CERTIFICATION_SCHEMES]
    cache.set(key, schemes, settings.DCR["REFERENCE_CACHE_SECONDS"])
    return schemes
