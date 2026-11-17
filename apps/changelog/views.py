from django.core.exceptions import PermissionDenied
from django.views.generic import TemplateView

from .models import PartnerTrack, Release, RoadmapItem

VIEW_PERMISSION = "changelog.view_release"


def _day(value) -> str:
    return f"{value.day} {value:%b %Y}"


class ReleaseNotesView(TemplateView):
    """
    One page for the team: releases (newest first), the roadmap and partner threads.
    Wrapped in admin.site.admin_view, so only staff reach it; also needs view rights
    on releases (superusers and the "Release notes" group).
    """

    template_name = "changelog/release_notes.html"
    admin_site = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_perm(VIEW_PERMISSION):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.admin_site is not None:
            context.update(self.admin_site.each_context(self.request))
        roadmap = list(RoadmapItem.objects.all())
        tracks = list(PartnerTrack.objects.all())
        partners: dict[str, list[PartnerTrack]] = {}
        for track in tracks:
            partners.setdefault(track.partner, []).append(track)
        releases = list(Release.objects.filter(published=True))
        waiting = sum(t.status == PartnerTrack.Status.WAITING_PARTNER for t in tracks)
        context.update(
            {
                "title": "Release notes",
                "kpis": [
                    ("Latest release", _day(releases[0].released_on) if releases else "-"),
                    ("Open roadmap items", sum(i.status != "done" for i in roadmap)),
                    ("Waiting on partners", waiting),
                    ("Partners", len(partners)),
                ],
                "releases": releases,
                "roadmap_open": [i for i in roadmap if i.status != RoadmapItem.Status.DONE],
                "roadmap_done": [i for i in roadmap if i.status == RoadmapItem.Status.DONE],
                "partners": partners,
                "can_edit": self.request.user.has_perm("changelog.change_release"),
            }
        )
        return context
