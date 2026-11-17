"""
Records that hang off an activity on DCR and are kept in step with the project on submit:
vocabulary links (practices, co-benefits, SDGs) and activity media.

The DCR ids of what was created are kept in `project.dcr_links`, keyed by entity and by the
local value (vocabulary id or media link), so a resubmit creates what is new, deletes what
was removed and leaves the rest alone.
"""

import logging

from apps.core.models import new_dcr_id
from apps.dcr import reference
from apps.dcr.exceptions import DCRError

from . import dcr_mapping
from .models import Project

logger = logging.getLogger(__name__)

# (DCR link entity, DCR property holding the vocabulary id, project field, vocabulary name)
# DCR is renaming plural entities to singular ones (Nov 2026). Entity and property names are
# candidates, tried in order against the live schema: expected singular name first.
VOCABULARY_LINKS = [
    (
        ("activity_technology_practice_process", "activity_technologies_practices_processes"),
        ("technology_practice_process_id", "technologies_practices_processes_id"),
        "technologies_practices_processes",
        "technologies_practices_processes",
    ),
    (("activity_co_benefit",), ("co_benefit_id",), "cobenefits", "co_benefits"),
    (
        # was `sustainable_development_goals` until Nov 2026 (no longer readable on DCR)
        ("activity_sustainable_development_goal", "sustainable_development_goals"),
        ("sustainable_development_goal_id",),
        "sustainable_development_goals",
        "sustainable_development_goals",
    ),
]


def resolve_entity(client, candidates) -> str | None:
    """The first candidate DCR declares; the last one when DCR cannot say; None if none exist."""
    known = [client.entity_exists(name) for name in candidates]
    for name, exists in zip(candidates, known, strict=True):
        if exists:
            return name
    return candidates[-1] if None in known else None


def resolve_property(client, entity: str, candidates) -> str:
    fields = client.entity_fields(entity) or set()
    return next((name for name in candidates if name in fields), candidates[-1])


MEDIA = "activity_media"


class LinkError(Exception):
    def __init__(self, step: str, detail: str, status_code: int | None = None):
        super().__init__(f"{step}: {detail}")
        self.step, self.detail, self.status_code = step, detail, status_code


def vocabulary_ids(name: str) -> set[str] | None:
    """Ids DCR currently publishes for a vocabulary; None when DCR cannot say."""
    items = reference.vocabulary(name)
    return None if items is None else {item["value"] for item in items}


def unlinked_values(project: Project) -> dict[str, list[str]]:
    """Per project field, the values that are not DCR vocabulary ids (sent as text only)."""
    out = {}
    for _entity, _prop, field, vocabulary in VOCABULARY_LINKS:
        known = vocabulary_ids(vocabulary)
        if known is None:
            continue
        unknown = [v for v in getattr(project, field) or [] if v not in known]
        if unknown:
            out[field] = unknown
    return out


def sync_links(client, token: str, project: Project) -> None:
    """Create and delete link records so DCR matches the project. Raises LinkError."""
    links = dict(project.dcr_links or {})
    retired = set()
    for entity_names, prop_names, field, vocabulary in VOCABULARY_LINKS:
        entity = resolve_entity(client, entity_names)
        for old in entity_names:
            if old != entity and links.pop(old, None) is not None:
                retired.add(old)  # records under a retired name went with it
        if entity is None:
            continue
        prop = resolve_property(client, entity, prop_names)
        known = vocabulary_ids(vocabulary)
        if known is None:
            logger.warning("vocabulary %s unavailable; %s links left unchanged", vocabulary, entity)
            continue
        wanted = [v for v in getattr(project, field) or [] if v in known]

        def vocabulary_record(value, prop=prop):
            # DCR assigns the id of a vocabulary link record
            return {"activity_id": project.dcr_id, prop: value}, None

        _reconcile(client, token, project, links, entity, wanted, vocabulary_record)
    if retired:
        project.dcr_links = links
        project.save(update_fields=["dcr_links", "updated_at"])
    if client.entity_exists(MEDIA) is not False:
        items = {dcr_mapping.media_link(m): m for m in project.media_links or []}
        existing = links.get(MEDIA, {})
        # media records carry descriptive fields, so existing ones are updated too
        for link, media_id in list(existing.items()):
            if link in items:
                _call(
                    client.update_entity,
                    MEDIA,
                    MEDIA,
                    media_id,
                    dcr_mapping.activity_media_payload(project, items[link], media_id),
                    token,
                )

        def media_record(link):
            # media records take a client-supplied id, like the main entities
            media_id = new_dcr_id("am")
            return dcr_mapping.activity_media_payload(project, items[link], media_id), media_id

        _reconcile(client, token, project, links, MEDIA, list(items), media_record)


def _reconcile(client, token, project, links, entity, wanted, build) -> None:
    current: dict[str, str] = dict(links.get(entity, {}))
    for value, record_id in list(current.items()):
        if value not in wanted:
            try:
                _call(client.delete_entity, entity, entity, record_id, token)
            except LinkError as exc:
                if exc.status_code != 404:  # already gone from DCR is fine
                    raise
            del current[value]
            _save(project, links, entity, current)
    for value in wanted:
        if value in current:
            continue
        payload, own_id = build(value)
        response = _call(client.create_entity, entity, entity, payload, token)
        record_id = own_id or client.entity_id_from(response, entity)
        if not record_id:
            raise LinkError(entity, "DCR did not return an id for the new record")
        current[value] = record_id
        _save(project, links, entity, current)


def _call(method, step: str, *args):
    try:
        return method(*args)
    except DCRError as exc:
        raise LinkError(step, str(exc), exc.status_code) from exc


def _save(project: Project, links: dict, entity: str, current: dict) -> None:
    """Persist after every DCR write, so a failure halfway never loses track of a record."""
    links[entity] = current
    project.dcr_links = links
    project.save(update_fields=["dcr_links", "updated_at"])
