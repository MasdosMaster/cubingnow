from django.db.models import Q
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import Record
from .serializers import RecordSerializer


class RecordViewSet(ReadOnlyModelViewSet):
    serializer_class = RecordSerializer

    def get_queryset(self):
        queryset = Record.objects.select_related(
            "result__competitor", "result__competition"
        ).all()
        level = self.request.query_params.get("level")
        status = self.request.query_params.get("status", Record.Status.ACTIVE)
        query = self.request.query_params.get("q")
        if level:
            queryset = queryset.filter(level=level.upper())
        if status:
            queryset = queryset.filter(status=status)
        if query:
            queryset = queryset.filter(
                Q(result__competitor__name__icontains=query)
                | Q(result__competitor__wca_id__icontains=query)
                | Q(result__event_name__icontains=query)
                | Q(result__competition__name__icontains=query)
            )
        return queryset

