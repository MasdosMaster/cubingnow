from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import Attendance, Competitor
from .serializers import AttendanceSerializer, CompetitorSerializer


class CompetitorViewSet(ReadOnlyModelViewSet):
    queryset = Competitor.objects.select_related("featured_profile").all()
    serializer_class = CompetitorSerializer
    lookup_field = "wca_id"


class AttendanceViewSet(ReadOnlyModelViewSet):
    queryset = Attendance.objects.select_related("competitor", "competition").all()
    serializer_class = AttendanceSerializer

