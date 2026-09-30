"""
Reference vocabularies for the frontend. Values come from the TESOBE field sheet where it
defines them (activity types, unit types); practices and co-benefits are examples only
until DCR publishes a controlled list, so free text is accepted for those.
"""

import pycountry

ACTIVITY_TYPES = [
    ("CARBON_FARMING", "Carbon farming"),
    ("PERMANENT_REMOVAL", "Permanent carbon removal"),
    ("CARBON_STORAGE_IN_PRODUCTS", "Carbon storage in products"),
]

# unit type produced by the activity -> activity type it implies
UNIT_TYPES = {
    "Permanent Removal": "PERMANENT_REMOVAL",
    "Carbon Farming Sequestration": "CARBON_FARMING",
    "Soil Emission Reduction": "CARBON_FARMING",
    "Carbon Storage in Product": "CARBON_STORAGE_IN_PRODUCTS",
}

VERIFICATION_STATUSES = ["in_progress", "verified", "failed"]

PRACTICE_EXAMPLES = ["cover_cropping", "reduced_tillage", "precision_fertilization"]
COBENEFIT_EXAMPLES = ["biodiversity", "water_quality", "soil_health"]


def is_valid_country(code: str) -> bool:
    return bool(code) and pycountry.countries.get(alpha_2=code.upper()) is not None


def countries() -> list[dict]:
    return sorted(
        ({"code": c.alpha_2, "name": c.name} for c in pycountry.countries),
        key=lambda c: c["name"],
    )


def activity_type_for_unit(unit_type: str) -> str | None:
    return UNIT_TYPES.get(unit_type)
