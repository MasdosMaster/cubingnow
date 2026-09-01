from rest_framework import serializers

from apps.competitions.serializers import CompetitionSerializer
from apps.competitors.geography import continent_for_country_code
from apps.competitors.serializers import CompetitorSerializer

from .models import Achievement, RecentRecordObservation, Record


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


class RecentRecordObservationSerializer(serializers.ModelSerializer):
    source_name = serializers.SerializerMethodField()
    matched_in_other_pipeline = serializers.SerializerMethodField()
    other_pipeline_detected_at = serializers.DateTimeField(read_only=True, allow_null=True)
    detection_time_difference_seconds = serializers.SerializerMethodField()
    matching_pipelines = serializers.SerializerMethodField()
    detection_timestamps_by_pipeline = serializers.SerializerMethodField()
    detection_time_deltas_seconds = serializers.SerializerMethodField()

    class Meta:
        model = RecentRecordObservation
        fields = [
            "id",
            "stable_result_identity",
            "canonical_key",
            "canonical_result",
            "ingestion_method",
            "source",
            "source_name",
            "source_result_id",
            "source_competition_id",
            "source_competitor_id",
            "wca_live_record_id",
            "wca_live_result_id",
            "wca_live_competition_id",
            "wca_competition_id",
            "competition_name",
            "competition_start_date",
            "competition_end_date",
            "round_id",
            "round_number",
            "round_name",
            "event_id",
            "event_name",
            "competitor_name",
            "competitor_wca_id",
            "competitor_wca_live_id",
            "country_code",
            "kind",
            "raw_result",
            "formatted_result",
            "record_level",
            "status",
            "source_url",
            "source_update_timestamp",
            "first_observed_at",
            "detected_at",
            "last_observed_at",
            "persisted_at",
            "updated_at",
            "withdrawn_at",
            "matched_in_other_pipeline",
            "other_pipeline_detected_at",
            "detection_time_difference_seconds",
            "matching_pipelines",
            "detection_timestamps_by_pipeline",
            "detection_time_deltas_seconds",
        ]

    @staticmethod
    def get_source_name(obj):
        if obj.ingestion_method == RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET:
            return "CubingChina Live"
        return "WCA Live"

    @staticmethod
    def get_matched_in_other_pipeline(obj):
        return obj.other_pipeline_detected_at is not None

    @staticmethod
    def get_detection_time_difference_seconds(obj):
        if obj.other_pipeline_detected_at is None:
            return None
        return (obj.detected_at - obj.other_pipeline_detected_at).total_seconds()

    @staticmethod
    def _pipeline_matches(obj):
        if not hasattr(obj, "_other_pipeline_matches"):
            obj._other_pipeline_matches = list(
                RecentRecordObservation.objects.filter(canonical_key=obj.canonical_key)
                .exclude(ingestion_method=obj.ingestion_method)
                .order_by("ingestion_method", "detected_at")
                .values("ingestion_method", "detected_at")
            )
        return obj._other_pipeline_matches

    def get_matching_pipelines(self, obj):
        return list(dict.fromkeys(row["ingestion_method"] for row in self._pipeline_matches(obj)))

    def get_detection_timestamps_by_pipeline(self, obj):
        rows = [{"ingestion_method": obj.ingestion_method, "detected_at": obj.detected_at}]
        rows.extend(self._pipeline_matches(obj))
        timestamps = {}
        for row in rows:
            timestamps.setdefault(
                row["ingestion_method"],
                row["detected_at"].isoformat().replace("+00:00", "Z"),
            )
        return timestamps

    def get_detection_time_deltas_seconds(self, obj):
        deltas = {}
        for row in self._pipeline_matches(obj):
            deltas.setdefault(
                row["ingestion_method"],
                (obj.detected_at - row["detected_at"]).total_seconds(),
            )
        return deltas


class AchievementSerializer(serializers.ModelSerializer):
    canonical_result_id = serializers.IntegerField(source="result_id", read_only=True)
    canonical_key = serializers.CharField(source="result.identity_key", read_only=True)
    record_level = serializers.CharField(source="type", read_only=True)
    validation_status = serializers.CharField(
        source="result.validation_status", read_only=True
    )
    validation_reason = serializers.CharField(
        source="result.validation_reason", read_only=True
    )
    wca_competition_id = serializers.CharField(
        source="result.wca_competition_id", read_only=True
    )
    competition_name = serializers.CharField(
        source="result.competition_name", read_only=True
    )
    round_id = serializers.CharField(source="result.round_id", read_only=True)
    round_number = serializers.IntegerField(
        source="result.round_number", read_only=True, allow_null=True
    )
    round_name = serializers.CharField(source="result.round_name", read_only=True)
    event_id = serializers.CharField(source="result.event_id", read_only=True)
    event_name = serializers.CharField(source="result.event_name", read_only=True)
    competitor_name = serializers.CharField(
        source="result.competitor_name", read_only=True
    )
    competitor_wca_id = serializers.CharField(
        source="result.competitor_wca_id", read_only=True
    )
    country_code = serializers.CharField(source="result.country_code", read_only=True)
    continent = serializers.SerializerMethodField()
    kind = serializers.CharField(source="result.kind", read_only=True)
    raw_result = serializers.IntegerField(source="result.value", read_only=True)
    formatted_result = serializers.CharField(
        source="result.formatted_result", read_only=True
    )
    entered_at = serializers.DateTimeField(
        source="result.entered_at", read_only=True, allow_null=True
    )
    observed_at = serializers.DateTimeField(
        source="result.first_observed_at", read_only=True
    )
    detected_at = serializers.SerializerMethodField()
    source_url = serializers.CharField(source="result.source_url", read_only=True)
    sources = serializers.SerializerMethodField()
    source_claims = serializers.SerializerMethodField()
    homepage_reason = serializers.CharField(
        source="qualification.homepage_reason", read_only=True
    )
    notification_eligible = serializers.BooleanField(
        source="qualification.notification_eligible", read_only=True
    )
    notification_reason = serializers.CharField(
        source="qualification.notification_reason", read_only=True
    )

    class Meta:
        model = Achievement
        fields = [
            "id",
            "canonical_result_id",
            "canonical_key",
            "record_level",
            "status",
            "classification_reason",
            "source_claim_supported",
            "benchmark_value",
            "validation_status",
            "validation_reason",
            "wca_competition_id",
            "competition_name",
            "round_id",
            "round_number",
            "round_name",
            "event_id",
            "event_name",
            "competitor_name",
            "competitor_wca_id",
            "country_code",
            "continent",
            "kind",
            "raw_result",
            "formatted_result",
            "entered_at",
            "observed_at",
            "detected_at",
            "source_url",
            "sources",
            "source_claims",
            "homepage_reason",
            "notification_eligible",
            "notification_reason",
        ]

    def get_continent(self, obj):
        return continent_for_country_code(obj.result.country_code)

    @staticmethod
    def get_detected_at(obj):
        return obj.result.entered_at or obj.result.first_observed_at

    @staticmethod
    def get_sources(obj):
        return sorted(
            set(obj.result.observations.values_list("ingestion_method", flat=True))
        )

    @staticmethod
    def get_source_claims(obj):
        return [
            {
                "ingestion_method": row.ingestion_method,
                "source_record_tag": row.source_record_tag or None,
                "claim_trusted": row.source_claim_trusted,
                "result_evidence_trusted": row.result_evidence_trusted,
                "entered_at": row.entered_at,
                "observed_at": row.last_observed_at,
            }
            for row in obj.result.observations.all().order_by("ingestion_method", "pk")
        ]
