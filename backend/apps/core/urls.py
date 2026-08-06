from django.conf import settings
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.notifications.providers import push_provider_is_configured


@api_view(["GET"])
def api_root(request):
    return Response(
        {
            "name": "CubingNow API",
            "version": "v1",
            "endpoints": {
                "records": request.build_absolute_uri("records/"),
                "recent_records": request.build_absolute_uri("recent-records/"),
                "ingestion_status": request.build_absolute_uri("ingestion-status/"),
                "competitions": request.build_absolute_uri("competitions/"),
                "competitors": request.build_absolute_uri("competitors/"),
                "competing_this_weekend": request.build_absolute_uri("competing-this-weekend/"),
                "notifications": request.build_absolute_uri("notifications/"),
            },
        }
    )


@api_view(["GET"])
def health(request):
    return Response(
        {
            "status": "ok",
            "notifications": {
                "provider": settings.PUSH_NOTIFICATION_PROVIDER,
                "web_push_configured": push_provider_is_configured(),
            },
        }
    )


urlpatterns = [path("", api_root), path("health/", health)]
