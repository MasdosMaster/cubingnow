import re

from rest_framework import serializers

from apps.competitions.serializers import CompetitionSerializer
from apps.competitors.geography import (
    continent_for_country_code,
    country_names_for_country_code,
)
from apps.competitors.serializers import CompetitorSerializer

from .models import ProcessedResultRecordLevel, RecentRecordObservation, Record


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


class NullableCharField(serializers.CharField):
    """Represent absent text consistently as JSON null instead of an empty string."""

    def to_representation(self, value):
        return super().to_representation(value) or None


class CanonicalRevisionReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="canonical_result_revision_id", read_only=True)
    number = serializers.IntegerField(source="canonical_revision", read_only=True)


class CanonicalResultReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="canonical_result_id", read_only=True)
    key = serializers.CharField(source="identity_key", read_only=True)
    revision = CanonicalRevisionReferenceSerializer(source="*", read_only=True)


class RecordHoldingSerializer(serializers.Serializer):
    current = serializers.BooleanField(source="currently_holds", read_only=True)
    shared_tie = serializers.BooleanField(source="is_shared_tie", read_only=True)
    ceased_reason = NullableCharField(source="ceased_holding_reason", read_only=True)
    ceased_by_processed_result_id = serializers.IntegerField(
        source="ceased_holding_by_id", read_only=True, allow_null=True
    )
    superseded_by_processed_result_id = serializers.IntegerField(
        source="superseded_by_id", read_only=True, allow_null=True
    )


class RecordAchievementSerializer(serializers.Serializer):
    level = serializers.CharField(source="record_level", read_only=True)
    scope = NullableCharField(source="record_scope", read_only=True)
    outcome = serializers.CharField(source="classification_outcome", read_only=True)
    recognition = serializers.CharField(source="recognition_status", read_only=True)
    incumbent_value = serializers.IntegerField(read_only=True, allow_null=True)
    holding = RecordHoldingSerializer(source="*", read_only=True)


TRAILING_NATIVE_NAME_PATTERN = re.compile(
    r"^(?P<romanized>.+?)\s*\((?P<native>[^()]*)\)\s*$"
)


def split_competitor_name(name):
    stripped_name = (name or "").strip()
    match = TRAILING_NATIVE_NAME_PATTERN.match(stripped_name)
    if not match:
        return stripped_name, None

    romanized_name = match.group("romanized").strip()
    native_name = match.group("native").strip()
    if not romanized_name or not native_name:
        return stripped_name, None
    return romanized_name, native_name


class RecordCompetitorSerializer(serializers.Serializer):
    name = serializers.CharField(source="competitor_name", read_only=True)
    romanized_name = serializers.SerializerMethodField()
    native_name = serializers.SerializerMethodField()
    wca_id = NullableCharField(source="competitor_wca_id", read_only=True)
    country_code = NullableCharField(read_only=True)
    country_display_name = serializers.SerializerMethodField()
    country_wca_name = serializers.SerializerMethodField()
    continent = serializers.SerializerMethodField()

    @staticmethod
    def get_romanized_name(obj):
        romanized_name, _native_name = split_competitor_name(obj.competitor_name)
        return romanized_name

    @staticmethod
    def get_native_name(obj):
        _romanized_name, native_name = split_competitor_name(obj.competitor_name)
        return native_name

    @staticmethod
    def get_country_display_name(obj):
        display_name, _wca_name = country_names_for_country_code(obj.country_code)
        return display_name or None

    @staticmethod
    def get_country_wca_name(obj):
        _display_name, wca_name = country_names_for_country_code(obj.country_code)
        return wca_name or None

    @staticmethod
    def get_continent(obj):
        return continent_for_country_code(obj.country_code) or None


class TimezoneResolutionSerializer(serializers.Serializer):
    status = serializers.CharField(source="timezone_resolution_status", read_only=True)
    reason = NullableCharField(source="timezone_resolution_reason", read_only=True)


class RecordCompetitionSerializer(serializers.Serializer):
    wca_id = NullableCharField(source="wca_competition_id", read_only=True)
    name = serializers.CharField(source="competition_name", read_only=True)
    timezone = NullableCharField(source="competition_timezone", read_only=True)
    local_date = serializers.DateField(
        source="competition_local_date", read_only=True, allow_null=True
    )
    timezone_resolution = TimezoneResolutionSerializer(source="*", read_only=True)


class RecordRoundSerializer(serializers.Serializer):
    id = NullableCharField(source="round_id", read_only=True)
    number = serializers.IntegerField(source="round_number", read_only=True, allow_null=True)
    name = NullableCharField(source="round_name", read_only=True)


class RecordEventSerializer(serializers.Serializer):
    id = serializers.CharField(source="event_id", read_only=True)
    name = serializers.CharField(source="event_name", read_only=True)


class RecordResultSerializer(serializers.Serializer):
    kind = serializers.CharField(read_only=True)
    raw = serializers.IntegerField(source="value", read_only=True)
    formatted = serializers.CharField(source="formatted_result", read_only=True)
    valid = serializers.BooleanField(source="is_valid_result", read_only=True)
    invalidity_reason = NullableCharField(read_only=True)


class RecordTimestampsSerializer(serializers.Serializer):
    entered_at = serializers.DateTimeField(read_only=True, allow_null=True)
    first_observed_at = serializers.DateTimeField(read_only=True)
    last_observed_at = serializers.DateTimeField(read_only=True)
    classified_at = serializers.DateTimeField(read_only=True)


class RecordValidationSerializer(serializers.Serializer):
    status = serializers.CharField(source="validation_status", read_only=True)
    reason = NullableCharField(source="validation_reason", read_only=True)


class SourceClaimTimestampsSerializer(serializers.Serializer):
    entered_at = serializers.DateTimeField(read_only=True, allow_null=True)
    first_observed_at = serializers.DateTimeField(read_only=True)
    last_observed_at = serializers.DateTimeField(read_only=True)


class SourceClaimSerializer(serializers.Serializer):
    pipeline = serializers.CharField(source="ingestion_method", read_only=True)
    record_tag = NullableCharField(source="source_record_tag", read_only=True)
    claim_trusted = serializers.BooleanField(source="source_claim_trusted", read_only=True)
    result_evidence_trusted = serializers.BooleanField(read_only=True)
    timestamps = SourceClaimTimestampsSerializer(source="*", read_only=True)


class RecordSourcesSerializer(serializers.Serializer):
    url = serializers.SerializerMethodField()
    pipelines = serializers.SerializerMethodField()
    claims = serializers.SerializerMethodField()

    @staticmethod
    def _observations(obj):
        return sorted(
            obj.processed_result.canonical_result.observations.all(),
            key=lambda row: (row.ingestion_method, row.pk),
        )

    @staticmethod
    def get_url(obj):
        return obj.processed_result.source_url or None

    def get_pipelines(self, obj):
        return sorted({row.ingestion_method for row in self._observations(obj)})

    def get_claims(self, obj):
        return SourceClaimSerializer(self._observations(obj), many=True).data


def _notification_eligible(obj):
    trusted_result = (
        obj.processed_result.is_valid_result
        and obj.processed_result.validation_status == "verified"
        and obj.processed_result.validation_reason == "trusted_source_observation"
    )
    independently_verified = getattr(obj, "_independently_verified", None)
    if independently_verified is None:
        independently_verified = obj.processed_result.canonical_result.record_validations.filter(
            level=obj.record_level,
            result_value=obj.processed_result.value,
            status="verified",
        ).exists()
    return (
        obj.processed_result.is_valid_result
        and (trusted_result or independently_verified)
        and obj.record_level in {"WR", "CR", "NR"}
        and obj.recognition_status == "recognized"
        and obj.classification_outcome != "none"
    )


class RecordNotificationSerializer(serializers.Serializer):
    eligible = serializers.SerializerMethodField()
    reason = serializers.SerializerMethodField()

    @staticmethod
    def get_eligible(obj):
        return _notification_eligible(obj)

    @staticmethod
    def get_reason(obj):
        return "eligible" if _notification_eligible(obj) else obj.recognition_status


class ProcessedResultRecordLevelSerializer(serializers.ModelSerializer):
    canonical_result = CanonicalResultReferenceSerializer(
        source="processed_result", read_only=True
    )
    achievement = RecordAchievementSerializer(source="*", read_only=True)
    competitor = RecordCompetitorSerializer(source="processed_result", read_only=True)
    competition = RecordCompetitionSerializer(source="processed_result", read_only=True)
    event = RecordEventSerializer(source="processed_result", read_only=True)
    round = RecordRoundSerializer(source="processed_result", read_only=True)
    result = RecordResultSerializer(source="processed_result", read_only=True)
    timestamps = RecordTimestampsSerializer(source="processed_result", read_only=True)
    validation = RecordValidationSerializer(source="processed_result", read_only=True)
    sources = RecordSourcesSerializer(source="*", read_only=True)
    notification = RecordNotificationSerializer(source="*", read_only=True)

    class Meta:
        model = ProcessedResultRecordLevel
        fields = [
            "id",
            "canonical_result",
            "achievement",
            "competitor",
            "competition",
            "event",
            "round",
            "result",
            "timestamps",
            "validation",
            "sources",
            "notification",
        ]
