from django.urls import path

from .views import (
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
        "partner/documents/<int:pk>/download/",
        PartnerDocumentDownloadView.as_view(),
        name="partner-document-download",
    ),
]
