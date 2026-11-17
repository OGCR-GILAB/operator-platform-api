from django.urls import path

from .reference_views import CountryListView, ReferenceView, VocabularyListView
from .views import HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("reference/", ReferenceView.as_view(), name="reference"),
    path("reference/countries/", CountryListView.as_view(), name="reference-countries"),
    path("reference/vocabularies/", VocabularyListView.as_view(), name="reference-vocabularies"),
]
