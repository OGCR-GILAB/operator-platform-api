from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    # the API has no landing page; send visitors to the interactive documentation
    path("", RedirectView.as_view(url="/api/docs/", permanent=False), name="root"),
    path("api/", RedirectView.as_view(url="/api/docs/", permanent=False), name="api-root"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/auth/", include("apps.dcr.urls")),
    path("api/", include("apps.operators.urls")),
    path("api/", include("apps.projects.urls")),
    path("api/", include("apps.parcels.urls")),
    path("api/", include("apps.documents.urls")),
    path("api/", include("apps.partners.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
