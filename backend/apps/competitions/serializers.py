from rest_framework import serializers

from .models import Competition


class CompetitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Competition
        fields = [
            "source_key",
            "wca_id",
            "name",
            "country_code",
            "city",
            "start_date",
            "end_date",
            "timezone",
        ]
