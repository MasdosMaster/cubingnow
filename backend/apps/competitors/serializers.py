from rest_framework import serializers

from apps.competitions.serializers import CompetitionSerializer

from .models import Attendance, Competitor


class CompetitorSerializer(serializers.ModelSerializer):
    featured = serializers.BooleanField(source="featured_profile.is_active", read_only=True)

    class Meta:
        model = Competitor
        fields = ["wca_id", "name", "country_code", "continent", "featured"]


class AttendanceSerializer(serializers.ModelSerializer):
    competitor = CompetitorSerializer(read_only=True)
    competition = CompetitionSerializer(read_only=True)

    class Meta:
        model = Attendance
        fields = [
            "id",
            "competitor",
            "competition",
            "observed_at",
            "is_accepted",
            "sources",
        ]
