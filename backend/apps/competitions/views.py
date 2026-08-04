from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import Competition
from .serializers import CompetitionSerializer


class CompetitionViewSet(ReadOnlyModelViewSet):
    queryset = Competition.objects.all()
    serializer_class = CompetitionSerializer
    lookup_field = "wca_id"

