from django.urls import path

from .views import (
    ActivityByRegistryIdView,
    ParcelByRegistryIdView,
    ParcelSearchView,
    PartnerDocsView,
    PartnerDocumentDownloadView,
    PartnerIndexView,
    PartnerPingView,
)

urlpatterns = [
    path("partner/", PartnerIndexView.as_view(), name="partner-index"),
    path("partner/docs/", PartnerDocsView.as_view(), name="partner-docs"),
    path("partner/ping/", PartnerPingView.as_view(), name="partner-ping"),
    path("partner/parcels/search/", ParcelSearchView.as_view(), name="partner-parcel-search"),
    path(
        "partner/activities/<str:dcr_id>/",
        ActivityByRegistryIdView.as_view(),
        name="partner-activity",
    ),
    path("partner/parcels/<str:dcr_id>/", ParcelByRegistryIdView.as_view(), name="partner-parcel"),
    path(
        "partner/documents/<int:pk>/download/",
        PartnerDocumentDownloadView.as_view(),
        name="partner-document-download",
    ),
]
