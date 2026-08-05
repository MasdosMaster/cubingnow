from django.urls import path

from .views import RecentRecordObservationViewSet, ingestion_status, record_comparison

record_list = RecentRecordObservationViewSet.as_view({"get": "list"})

urlpatterns = [
    path("", record_list, name="recent-record-observations"),
    path("comparison/", record_comparison, name="recent-record-comparison"),
    path("status/", ingestion_status, name="ingestion-status-alias"),
]
