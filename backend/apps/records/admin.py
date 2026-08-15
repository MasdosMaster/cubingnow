from django.contrib import admin

from .models import (
    Achievement,
    CanonicalResult,
    ClassificationScopeWork,
    CubingChinaCompetitionTarget,
    CubingChinaResultState,
    CubingChinaRoundTarget,
    IngestionRun,
    IngestionWorkerStatus,
    PersonalBestBaseline,
    QualificationDecision,
    RecentRecordObservation,
    Record,
    RecordBenchmark,
    RecordValidation,
    Result,
    ResultIdentityScope,
    ResultObservation,
    SourceObservation,
    SubscriptionResultState,
    SubscriptionRound,
    WCARecordSnapshot,
)

admin.site.register(Result)
admin.site.register(Record)
admin.site.register(IngestionRun)
admin.site.register(SourceObservation)
admin.site.register(RecentRecordObservation)
admin.site.register(SubscriptionRound)
admin.site.register(SubscriptionResultState)
admin.site.register(IngestionWorkerStatus)
admin.site.register(CubingChinaCompetitionTarget)
admin.site.register(CubingChinaRoundTarget)
admin.site.register(CubingChinaResultState)


@admin.register(CanonicalResult)
class CanonicalResultAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event_id",
        "kind",
        "formatted_result",
        "competitor_name",
        "competition_name",
        "validation_status",
        "status",
    )
    list_filter = ("event_id", "kind", "validation_status", "status")
    search_fields = (
        "identity_key",
        "competitor_name",
        "competitor_wca_id",
        "competition_name",
    )


@admin.register(ResultObservation)
class ResultObservationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "canonical_result",
        "ingestion_method",
        "kind",
        "value",
        "source_record_tag",
        "source_claim_trusted",
        "result_evidence_trusted",
        "status",
    )
    list_filter = ("ingestion_method", "kind", "source_record_tag", "status")
    search_fields = ("observation_key", "source_result_identity")


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "result",
        "type",
        "status",
        "classification_reason",
        "source_claim_supported",
    )
    list_filter = ("type", "status", "classification_reason")


admin.site.register(QualificationDecision)
admin.site.register(RecordBenchmark)
admin.site.register(RecordValidation)
admin.site.register(WCARecordSnapshot)
admin.site.register(PersonalBestBaseline)
admin.site.register(ResultIdentityScope)


@admin.register(ClassificationScopeWork)
class ClassificationScopeWorkAdmin(admin.ModelAdmin):
    list_display = (
        "event_id",
        "kind",
        "requested_version",
        "processed_version",
        "dirty_since",
        "claimed_by",
        "last_duration_ms",
        "last_result_count",
        "last_completed_at",
    )
    list_filter = ("kind",)
    search_fields = ("event_id", "claimed_by", "last_error")
