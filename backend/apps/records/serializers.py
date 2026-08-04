from rest_framework import serializers

from apps.competitions.serializers import CompetitionSerializer
from apps.competitors.serializers import CompetitorSerializer

from .models import Record


class RecordSerializer(serializers.ModelSerializer):
    competitor = CompetitorSerializer(source="result.competitor", read_only=True)
    competition = CompetitionSerializer(source="result.competition", read_only=True)
    event_id = serializers.CharField(source="result.event_id", read_only=True)
    event_name = serializers.CharField(source="result.event_name", read_only=True)
    result_kind = serializers.CharField(source="result.kind", read_only=True)
    result_value = serializers.IntegerField(source="result.value", read_only=True)
    display_value = serializers.SerializerMethodField()

    class Meta:
        model = Record
        fields = [
            "id",
            "level",
            "status",
            "detected_at",
            "competitor",
            "competition",
            "event_id",
            "event_name",
            "result_kind",
            "result_value",
            "display_value",
        ]

    def get_display_value(self, obj):
        # Preserve source encoding until event-specific WCA formatting is implemented.
        return str(obj.result.value)
