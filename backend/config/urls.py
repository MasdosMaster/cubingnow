from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/api/", permanent=False)),
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/records/", include("apps.records.urls")),
    path("api/recent-records/", include("apps.records.experimental_urls")),
    path("api/ingestion-status/", include("apps.records.status_urls")),
    path("api/competitions/", include("apps.competitions.urls")),
    path("api/competitors/", include("apps.competitors.urls")),
]
