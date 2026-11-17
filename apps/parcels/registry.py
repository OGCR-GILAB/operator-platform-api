"""
Owners of French parcels as the land registry knows them, for parcels owned by legal persons.

Source: DGFiP, "Fichiers des locaux et des parcelles des personnes morales", the
"fichier des parcelles" (unbuilt parcels), published yearly on data.economie.gouv.fr and
data.gouv.fr under the Licence Ouverte. It covers parcels with at least one right held by a
legal person (companies, GAEC/GFA/SCI, municipalities, the State, ...). Owners who are natural
persons are not published, so a parcel without a match is not proof of anything.

The rows are loaded with `manage.py load_registry_owners` and matched to our parcels by the
French cadastral id (IDU, 14 characters: department 2 + commune 3 + prefix 3 + section 2 +
plan number 4), which is what `Parcel.cadastral_reference` holds for French parcels.
"""

import csv
import io
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

SOURCE = "DGFiP - Fichiers des parcelles des personnes morales"
SOURCE_URL = (
    "https://www.data.gouv.fr/datasets/fichiers-des-locaux-et-des-parcelles-des-personnes-morales"
)
COVERAGE = (
    "Metropolitan France: parcels with a right held by a legal person; natural persons are not "
    "published."
)
# overseas departments (code 97 in the file) are not loaded
OVERSEAS = "97"

_ATTACHMENTS = (
    "https://data.economie.gouv.fr/api/v2/catalog/datasets/"
    "fichiers-des-locaux-et-des-parcelles-des-personnes-morales/attachments/"
)
# the parcel files of each vintage ("situation au 1er janvier"), split by department range
FILES = {
    2026: [
        _ATTACHMENTS + "fichiers_des_parcelles_dpts_1_a_30_situation_2026zip",
        _ATTACHMENTS + "fichier_des_parcelles_31_a_60_situation_2026zip",
        _ATTACHMENTS + "fichiers_des_parcelles_dpts_61_a_976_situation_2026zip",
    ],
}

# column positions in PM_AA_NB_DDD.csv (the header has two "Contenance" columns, so the
# positions are checked against the header instead of looked up by name)
COLUMNS = {
    "department": (0, "partement"),
    "commune": (2, "Code Commune"),
    "prefix": (4, "fixe"),
    "section": (5, "Section"),
    "plan": (6, "plan"),
    "right": (17, "Code droit"),
    "siren": (19, "SIREN"),
    "person_group": (20, "Groupe personne"),
    "legal_form_code": (21, "Forme juridique - par"),
    "legal_form": (22, "abr"),
    "legal_name": (23, "nomination"),
}


def normalize_idu(reference: str | None) -> str | None:
    """The 14-character IDU of a French cadastral reference, or None if it is not one."""
    value = "".join((reference or "").split()).upper()
    return value if len(value) == 14 and value[:2].isalnum() and value[2:5].isdigit() else None


def idu_of(row: list[str]) -> str:
    department = row[0].strip()
    commune = row[2].strip()
    prefix = row[4].strip() or "000"
    section = row[5].replace(" ", "0").strip().rjust(2, "0")
    return f"{department}{commune}{prefix.rjust(3, '0')}{section}{row[6].strip()}"


def _split(code: str) -> tuple[str, str]:
    """'P - Propriétaire' -> ('P', 'Propriétaire'); '1A' -> ('1A', '')."""
    code, _, label = code.partition(" - ")
    return code.strip(), label.strip()


def check_header(header: list[str]) -> None:
    for name, (index, fragment) in COLUMNS.items():
        if index >= len(header) or fragment.lower() not in header[index].lower():
            raise ValueError(f"unexpected column {index} for {name}: {header[index : index + 1]}")


def parse_csv(lines: Iterable[str]) -> Iterator[dict]:
    """
    Owner rows of one department file, one per parcel, holder and right. The file has a row
    per fiscal subdivision (SUF) of the parcel; those repeat the same holder and are merged.
    """
    reader = csv.reader(lines, delimiter=";")
    check_header(next(reader))
    seen = set()
    for row in reader:
        if len(row) < 24 or row[0].strip().startswith(OVERSEAS):
            continue
        idu = idu_of(row)
        if len(idu) != 14:
            continue
        right, right_label = _split(row[17])
        key = (idu, row[19].strip(), right, row[23].strip())
        if key in seen:
            continue
        seen.add(key)
        group_code, group_label = _split(row[20])
        yield {
            "idu": idu,
            "right_code": right[:2],
            "right_label": right_label[:80],
            "siren": row[19].strip()[:9],
            "person_group": (group_label or group_code)[:80],
            "legal_form_code": row[21].strip()[:8],
            "legal_form": row[22].strip()[:16],
            "legal_name": row[23].strip()[:255],
        }


def iter_files(paths: Iterable[Path]) -> Iterator[tuple[str, Iterator[dict]]]:
    """(file name, rows) for every department CSV in the given zips or CSV files."""
    for path in paths:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for name in sorted(archive.namelist()):
                    if name.lower().endswith(".csv"):
                        with archive.open(name) as handle:
                            text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
                            yield name, parse_csv(text)
        else:
            with open(path, encoding="utf-8-sig", newline="") as handle:
                yield path.name, parse_csv(handle)


def owners_by_idu(references: Iterable[str | None]) -> dict[str, list[dict]]:
    """Registry owners for the given cadastral references, keyed by IDU (one query)."""
    from .models import RegistryOwner

    idus = {idu for idu in map(normalize_idu, references) if idu}
    found: dict[str, list[dict]] = {idu: [] for idu in idus}
    if not idus:
        return found
    fields = (
        "idu",
        "legal_name",
        "siren",
        "legal_form",
        "person_group",
        "right_code",
        "right_label",
    )
    for row in (
        RegistryOwner.objects.filter(idu__in=idus).order_by("idu", "right_code", "legal_name")
    ).values(*fields):
        found[row.pop("idu")].append(row)
    return found


def loaded_vintage() -> int | None:
    """The vintage currently loaded (the loader keeps one at a time), None when empty."""
    from .models import RegistryOwner

    return RegistryOwner.objects.order_by().values_list("vintage", flat=True).first()


# person groups of the DGFiP file that are public bodies; the rest are legal persons
PUBLIC_GROUPS = ("Commune", "État", "Département", "Région", "Établissements publics")


def suggested_owner_type(person_group: str) -> str:
    """The `ParcelOwner.owner_type` a registry holder suggests."""
    group = (person_group or "").strip()
    return "public_entity" if group.startswith(PUBLIC_GROUPS) else "legal_person"


def normalize_name(name: str | None) -> str:
    """Upper case without accents or punctuation: 'Gfa des Varennes.' -> 'GFA DES VARENNES'."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def matches_declared(owners: list[dict], declared_name: str | None) -> bool | None:
    """
    Whether the declared owner is one of the registry's owners (right P), by normalized name.
    None when there is nothing to compare: no declaration, or no legal-person owner in the
    registry (the parcel is then most likely owned by natural persons, who are not published).
    """
    names = {normalize_name(o["legal_name"]) for o in owners if o["right_code"] == "P"}
    if not declared_name or not names:
        return None
    return normalize_name(declared_name) in names


def registry_block(
    reference: str | None,
    owners: dict[str, list[dict]],
    vintage: int | None,
    declared_name: str | None = None,
):
    """What the APIs return for one parcel; None when there is nothing to compare."""
    idu = normalize_idu(reference)
    if idu is None or vintage is None or idu.startswith(OVERSEAS):
        return None  # not a French parcel, no registry loaded, or not covered (overseas)
    rows = [
        {**row, "suggested_owner_type": suggested_owner_type(row["person_group"])}
        for row in owners.get(idu, [])
    ]
    return {
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "vintage": vintage,
        "coverage": COVERAGE,
        "cadastral_reference": idu,
        "owners": rows,
        "matches_declared": matches_declared(rows, declared_name),
    }
