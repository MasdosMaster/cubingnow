from django.urls import path

from .views import ingestion_status

urlpatterns = [path("", ingestion_status, name="ingestion-status")]
