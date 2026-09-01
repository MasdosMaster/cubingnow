from django.contrib import admin

from .models import (
    BaselineMetadata,
    BaselineRecordsAverage,
    BaselineRecordsSingle,
    CanonicalResult,
    CanonicalResultRevision,
    ClassificationWork,
    CubingChinaCompetitionTarget,
    CubingChinaDiffTable,
    CubingChinaRoundTarget,
    IngestionRun,
    IngestionWorkerStatus,
    LiveRecordsAverage,
    LiveRecordsSingle,
    ProcessedResult,
    ProcessedResultRecordLevel,
    RecentRecordObservation,
    Record,
    RecordValidation,
    Result,
    ResultIdentityScope,
    ResultObservation,
    SourceObservation,
    SubscriptionRound,
    WCALiveDiffTable,
    WCARecordSnapshot,
)

admin.site.register(Result)
admin.site.register(Record)
admin.site.register(IngestionRun)
admin.site.register(SourceObservation)
admin.site.register(RecentRecordObservation)
admin.site.register(SubscriptionRound)
admin.site.register(WCALiveDiffTable)
admin.site.register(IngestionWorkerStatus)
admin.site.register(CubingChinaCompetitionTarget)
admin.site.register(CubingChinaRoundTarget)
admin.site.register(CubingChinaDiffTable)


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


@admin.register(ProcessedResultRecordLevel)
class ProcessedResultRecordLevelAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "processed_result",
        "record_level",
        "classification_outcome",
        "recognition_status",
        "currently_holds",
    )
    list_filter = ("record_level", "classification_outcome", "recognition_status")


admin.site.register(RecordValidation)
admin.site.register(WCARecordSnapshot)
admin.site.register(ResultIdentityScope)
admin.site.register(CanonicalResultRevision)
admin.site.register(ProcessedResult)
admin.site.register(BaselineMetadata)
admin.site.register(BaselineRecordsSingle)
admin.site.register(BaselineRecordsAverage)
admin.site.register(LiveRecordsSingle)
admin.site.register(LiveRecordsAverage)


@admin.register(ClassificationWork)
class ClassificationWorkAdmin(admin.ModelAdmin):
    list_display = (
        "canonical_result",
        "revision",
        "action",
        "status",
        "attempts",
        "claimed_by",
        "completed_at",
    )
    list_filter = ("action", "status")
    search_fields = ("canonical_result__identity_key", "claimed_by", "last_error")
