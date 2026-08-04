from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def api_root(request):
    return Response(
        {
            "name": "CubingNow API",
            "version": "v1",
            "endpoints": {
                "records": request.build_absolute_uri("records/"),
                "competitions": request.build_absolute_uri("competitions/"),
                "competitors": request.build_absolute_uri("competitors/"),
            },
        }
    )


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


urlpatterns = [path("", api_root), path("health/", health)]
