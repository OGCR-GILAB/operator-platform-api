"""Dashboard, sidebar and environment badge for the django-unfold admin."""

from django.conf import settings
from django.db.models import Count
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _


def environment_callback(request):
    return ["Debug", "warning"] if settings.DEBUG else ["Production", "success"]


def navigation(request):
    return [
        {
            "title": _("Overview"),
            "items": [
                {"title": _("Dashboard"), "icon": "dashboard", "link": reverse_lazy("admin:index")},
                {"title": _("API docs"), "icon": "api", "link": "/api/docs/"},
            ],
        },
        {
            "title": _("Projects"),
            "separator": True,
            "items": [
                {
                    "title": _("Projects"),
                    "icon": "forest",
                    "link": reverse_lazy("admin:projects_project_changelist"),
                },
                {
                    "title": _("Activity plans"),
                    "icon": "assignment",
                    "link": reverse_lazy("admin:projects_activityplan_changelist"),
                },
                {
                    "title": _("Monitoring plans"),
                    "icon": "monitoring",
                    "link": reverse_lazy("admin:projects_monitoringplan_changelist"),
                },
            ],
        },
        {
            "title": _("Land"),
            "separator": True,
            "items": [
                {
                    "title": _("Parcels"),
                    "icon": "map",
                    "link": reverse_lazy("admin:parcels_parcel_changelist"),
                },
                {
                    "title": _("Project parcels"),
                    "icon": "link",
                    "link": reverse_lazy("admin:parcels_projectparcel_changelist"),
                },
                {
                    "title": _("Owner verifications"),
                    "icon": "verified",
                    "link": reverse_lazy("admin:parcels_parcelownerverification_changelist"),
                },
                {
                    "title": _("Documents"),
                    "icon": "description",
                    "link": reverse_lazy("admin:documents_document_changelist"),
                },
            ],
        },
        {
            "title": _("Organisations and access"),
            "separator": True,
            "items": [
                {
                    "title": _("Operators"),
                    "icon": "corporate_fare",
                    "link": reverse_lazy("admin:operators_operator_changelist"),
                },
                {
                    "title": _("Users"),
                    "icon": "group",
                    "link": reverse_lazy("admin:accounts_user_changelist"),
                },
                {
                    "title": _("Partner API keys"),
                    "icon": "key",
                    "link": reverse_lazy("admin:partners_partnerapikey_changelist"),
                },
            ],
        },
    ]


def dashboard_callback(request, context):
    from apps.documents.models import Document
    from apps.operators.models import Operator
    from apps.parcels.models import Parcel
    from apps.projects.models import Project

    by_status = dict(Project.objects.values_list("status").annotate(n=Count("id")))
    failed = (
        Project.objects.filter(dcr_sync_status="failed").count()
        + Parcel.objects.filter(dcr_sync_status="failed").count()
        + Operator.objects.filter(dcr_sync_status="failed").count()
    )
    context.update(
        {
            "kpi": [
                {
                    "title": "Operators",
                    "metric": Operator.objects.count(),
                    "link": reverse_lazy("admin:operators_operator_changelist"),
                },
                {
                    "title": "Projects",
                    "metric": Project.objects.count(),
                    "link": reverse_lazy("admin:projects_project_changelist"),
                },
                {
                    "title": "Submitted to DCR",
                    "metric": by_status.get("submitted", 0)
                    + by_status.get("accepted", 0)
                    + by_status.get("rejected", 0),
                    "link": "/admin/projects/project/?status__exact=submitted",
                },
                {
                    "title": "Parcels",
                    "metric": Parcel.objects.count(),
                    "link": reverse_lazy("admin:parcels_parcel_changelist"),
                },
                {
                    "title": "Documents",
                    "metric": Document.objects.filter(is_current=True).count(),
                    "link": reverse_lazy("admin:documents_document_changelist"),
                },
                {
                    "title": "Failed DCR syncs",
                    "metric": failed,
                    "link": "/admin/projects/project/?dcr_sync_status__exact=failed",
                    "alert": failed > 0,
                },
            ],
            "status_rows": [
                {"label": label, "value": value, "count": by_status.get(value, 0)}
                for value, label in Project.Status.choices
            ],
            "recent_projects": Project.objects.select_related("operator", "owner").order_by(
                "-updated_at"
            )[:8],
            "recent_documents": Document.objects.select_related("uploaded_by")
            .filter(is_current=True)
            .order_by("-uploaded_at")[:6],
        }
    )
    return context
