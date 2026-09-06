from django.core.exceptions import ValidationError
from django.db import models

from apps.competitions.models import Competition
from apps.competitors.models import Competitor


class Result(models.Model):
    class Kind(models.TextChoices):
        SINGLE = "single", "Single"
        AVERAGE = "average", "Average"

    source_id = models.CharField(max_length=255, unique=True)
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="results")
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name="results")
    event_id = models.CharField(max_length=16)
    event_name = models.CharField(max_length=128)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    value = models.IntegerField(help_text="Integer-encoded value received from the source")
    source_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Record(models.Model):
    class Level(models.TextChoices):
        WORLD = "WR", "World"
        CONTINENTAL = "CR", "Continental"
        NATIONAL = "NR", "National"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        WITHDRAWN = "withdrawn", "Withdrawn"

    result = models.OneToOneField(Result, on_delete=models.CASCADE, related_name="record")
    level = models.CharField(max_length=2, choices=Level.choices)
    detected_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-detected_at"]


class IngestionRun(models.Model):
    class Mode(models.TextChoices):
        API_POLLING = "api_polling", "API polling"
        GRAPHQL_SUBSCRIPTION = "graphql_subscription", "GraphQL subscription"
        CUBINGCHINA_WEBSOCKET = "cubingchina_websocket", "CubingChina WebSocket"
        SUBSCRIPTION = "subscription", "Subscription"
        RECONCILIATION = "reconciliation", "Reconciliation"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    mode = models.CharField(max_length=32, choices=Mode.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    observations_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)


class SourceObservation(models.Model):
    source = models.CharField(max_length=32, default="wca_live")
    ingestion_method = models.CharField(max_length=32, blank=True)
    external_id = models.CharField(max_length=255, blank=True)
    event_type = models.CharField(max_length=128)
    observed_at = models.DateTimeField()
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64)
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.SET_NULL, null=True, related_name="observations"
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-observed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "ingestion_method", "payload_hash"],
                name="unique_source_observation_per_ingestion_method",
            )
        ]


class ResultIdentityScope(models.Model):
    """Database-lockable scope used to serialize reconciliation/classification."""

    key = models.CharField(max_length=768, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class CanonicalResult(models.Model):
    """One finalized round-level best single or official average."""

    class Kind(models.TextChoices):
        SINGLE = "single", "Single"
        AVERAGE = "average", "Average"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CORRECTED = "corrected", "Corrected"
        RETRACTED = "retracted", "Retracted"

    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    identity_key = models.CharField(max_length=768, unique=True)
    identity_scope = models.ForeignKey(
        ResultIdentityScope,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="results",
    )
    wca_competition_id = models.CharField(max_length=64, blank=True, db_index=True)
    competition_name = models.CharField(max_length=255)
    competition_country_code = models.CharField(max_length=8, blank=True)
    competition_start_date = models.DateField(null=True, blank=True)
    competition_end_date = models.DateField(null=True, blank=True)
    round_id = models.CharField(max_length=64, blank=True)
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    round_name = models.CharField(max_length=128, blank=True)
    event_id = models.CharField(max_length=16, db_index=True)
    event_name = models.CharField(max_length=128)
    competitor_name = models.CharField(max_length=255)
    competitor_wca_id = models.CharField(max_length=16, blank=True, db_index=True)
    country_code = models.CharField(max_length=8, blank=True)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    # Transitional only. Finalized round-level rows always store NULL; remove after
    # the production backfill has been verified.
    attempt_number = models.PositiveSmallIntegerField(null=True, blank=True)
    value = models.IntegerField()
    formatted_result = models.CharField(max_length=128)
    entered_at = models.DateTimeField(null=True, blank=True)
    first_observed_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    source_url = models.URLField(max_length=512, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    validation_status = models.CharField(
        max_length=16,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    validation_reason = models.CharField(max_length=128, blank=True)
    competition_timezone = models.CharField(max_length=64, blank=True)
    competition_local_date = models.DateField(null=True, blank=True)
    timezone_resolution_status = models.CharField(max_length=16, default="unresolved")
    timezone_resolution_reason = models.CharField(max_length=64, blank=True)
    revision = models.PositiveIntegerField(default=1)
    current_observation = models.ForeignKey(
        "ResultObservation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for_results",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-entered_at", "-first_observed_at", "-id"]
        indexes = [
            models.Index(
                fields=["event_id", "kind", "status"],
                name="canonical_result_scope_idx",
            ),
            models.Index(
                fields=["event_id", "kind", "id"],
                condition=models.Q(status__in=["active", "corrected"]),
                name="active_result_scope_order_idx",
            ),
            models.Index(
                fields=[
                    "wca_competition_id",
                    "competitor_wca_id",
                    "event_id",
                    "round_number",
                    "kind",
                    "status",
                ],
                name="canonical_family_status_idx",
            ),
        ]


class CanonicalResultRevision(models.Model):
    """Immutable classifier-ready snapshot of one canonical result revision."""

    class Action(models.TextChoices):
        ACTIVE = "active", "Active"
        CORRECTED = "corrected", "Corrected"
        RETRACTED = "retracted", "Retracted"

    canonical_result = models.ForeignKey(
        CanonicalResult, on_delete=models.CASCADE, related_name="revisions"
    )
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=Action.choices)
    identity_key = models.CharField(max_length=768)
    wca_competition_id = models.CharField(max_length=64, blank=True, db_index=True)
    competition_name = models.CharField(max_length=255)
    competition_country_code = models.CharField(max_length=8, blank=True)
    competition_start_date = models.DateField(null=True, blank=True)
    competition_end_date = models.DateField(null=True, blank=True)
    competition_timezone = models.CharField(max_length=64, blank=True)
    competition_local_date = models.DateField(null=True, blank=True, db_index=True)
    timezone_resolution_status = models.CharField(max_length=16, default="unresolved")
    timezone_resolution_reason = models.CharField(max_length=64, blank=True)
    round_id = models.CharField(max_length=64, blank=True)
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    round_name = models.CharField(max_length=128, blank=True)
    event_id = models.CharField(max_length=16, db_index=True)
    event_name = models.CharField(max_length=128)
    competitor_name = models.CharField(max_length=255)
    competitor_wca_id = models.CharField(max_length=16, blank=True, db_index=True)
    country_code = models.CharField(max_length=8, blank=True)
    kind = models.CharField(max_length=8, choices=CanonicalResult.Kind.choices)
    value = models.IntegerField()
    formatted_result = models.CharField(max_length=128)
    entered_at = models.DateTimeField(null=True, blank=True)
    first_observed_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    source_url = models.URLField(max_length=512, blank=True)
    canonical_status = models.CharField(max_length=16, choices=CanonicalResult.Status.choices)
    validation_status = models.CharField(
        max_length=16, choices=CanonicalResult.ValidationStatus.choices
    )
    validation_reason = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["canonical_result_id", "revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["canonical_result", "revision"],
                name="unique_canonical_result_revision",
            )
        ]
        indexes = [
            models.Index(
                fields=["event_id", "kind", "entered_at", "first_observed_at"],
                name="canonical_revision_time_idx",
            )
        ]

    @property
    def classification_at(self):
        return self.entered_at or self.first_observed_at

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("CanonicalResultRevision rows are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("CanonicalResultRevision rows are retained indefinitely")


class ClassificationWork(models.Model):
    """Durable per-revision classifier queue."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        STALE = "stale", "Stale"

    canonical_result_revision = models.OneToOneField(
        CanonicalResultRevision,
        on_delete=models.CASCADE,
        related_name="classification_work",
    )
    canonical_result = models.ForeignKey(
        CanonicalResult, on_delete=models.CASCADE, related_name="classification_work"
    )
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=CanonicalResultRevision.Action.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    claimed_by = models.CharField(max_length=128, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["canonical_result", "revision"],
                name="unique_classification_work_revision",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "claim_expires_at", "created_at"],
                name="classification_work_ready_idx",
            ),
            models.Index(
                fields=["canonical_result", "revision", "status"],
                name="classification_work_order_idx",
            ),
        ]


class ResultObservation(models.Model):
    """Current normalized evidence for one finalized provider round-level claim.

    Immutable provider frames remain in :class:`SourceObservation`; this row is the
    restart-safe normalized projection used by reconciliation.
    """

    class Source(models.TextChoices):
        WCA_LIVE = "wca_live", "WCA Live"
        CUBINGCHINA = "cubingchina", "CubingChina"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETRACTED = "retracted", "Retracted"

    observation_key = models.CharField(max_length=768, unique=True)
    canonical_result = models.ForeignKey(
        CanonicalResult, on_delete=models.CASCADE, related_name="observations"
    )
    raw_observation = models.ForeignKey(
        SourceObservation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="normalized_results",
    )
    source = models.CharField(max_length=32, choices=Source.choices)
    ingestion_method = models.CharField(max_length=32)
    source_result_identity = models.CharField(max_length=255)
    source_competition_id = models.CharField(max_length=64, blank=True)
    source_competitor_id = models.CharField(max_length=64, blank=True)
    kind = models.CharField(max_length=8, choices=CanonicalResult.Kind.choices)
    # Transitional only. Finalized provider claims always store NULL.
    attempt_number = models.PositiveSmallIntegerField(null=True, blank=True)
    value = models.IntegerField()
    source_record_tag = models.CharField(max_length=8, blank=True)
    source_claim_trusted = models.BooleanField(default=False)
    result_evidence_trusted = models.BooleanField(default=False)
    entered_at = models.DateTimeField(null=True, blank=True)
    first_observed_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    revision = models.PositiveIntegerField(default=1)
    normalized_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_observed_at", "-id"]
        indexes = [
            models.Index(
                fields=["source", "ingestion_method", "source_result_identity"],
                name="result_observation_source_idx",
            ),
            models.Index(
                fields=["canonical_result"],
                condition=models.Q(source="cubingchina", status="active"),
                name="active_cc_result_idx",
            ),
        ]


class RecordLevel(models.TextChoices):
    WORLD = "WR", "World record"
    CONTINENTAL = "CR", "Continental record"
    NATIONAL = "NR", "National record"
    PERSONAL = "PR", "Personal record"


class _WideRecordValues(models.Model):
    record_holder = models.CharField(max_length=128)
    record_type = models.CharField(max_length=2, choices=RecordLevel.choices)

    class Meta:
        abstract = True


class BaselineRecordsSingle(_WideRecordValues):
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["record_holder", "record_type"], name="unique_baseline_single_holder"
            )
        ]
        indexes = [
            models.Index(
                fields=["record_holder", "record_type"], name="baseline_single_holder_idx"
            )
        ]


class BaselineRecordsAverage(_WideRecordValues):
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["record_holder", "record_type"], name="unique_baseline_average_holder"
            )
        ]
        indexes = [
            models.Index(
                fields=["record_holder", "record_type"], name="baseline_average_holder_idx"
            )
        ]


class LiveRecordsSingle(_WideRecordValues):
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["record_holder", "record_type"], name="unique_live_single_holder"
            )
        ]
        indexes = [
            models.Index(
                fields=["record_holder", "record_type"], name="live_single_holder_idx"
            )
        ]


class LiveRecordsAverage(_WideRecordValues):
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["record_holder", "record_type"], name="unique_live_average_holder"
            )
        ]
        indexes = [
            models.Index(
                fields=["record_holder", "record_type"], name="live_average_holder_idx"
            )
        ]


# Declaring the repetitive fixed event fields here keeps the Python/SQL mapping in
# one audited place while still producing ordinary concrete columns in migrations.
from .event_columns import AVERAGE_EVENT_IDS, EVENT_FIELD_BY_ID, SINGLE_EVENT_IDS

for _model in (BaselineRecordsSingle, LiveRecordsSingle):
    for _event_id in SINGLE_EVENT_IDS:
        _model.add_to_class(
            EVENT_FIELD_BY_ID[_event_id],
            models.IntegerField(null=True, blank=True, db_column=_event_id),
        )
for _model in (BaselineRecordsAverage, LiveRecordsAverage):
    for _event_id in AVERAGE_EVENT_IDS:
        _model.add_to_class(
            EVENT_FIELD_BY_ID[_event_id],
            models.IntegerField(null=True, blank=True, db_column=_event_id),
        )


class BaselineMetadata(models.Model):
    export_generated_at = models.DateTimeField()
    downloaded_at = models.DateTimeField()
    source_filename = models.CharField(max_length=255)
    source_version = models.CharField(max_length=255, blank=True)
    rebuilt_at = models.DateTimeField()
    absorbed_competition_ids = models.JSONField(default=list)
    is_active = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-rebuilt_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="single_active_baseline_metadata",
            )
        ]


class HistoricalResult(models.Model):
    """One rankable WCA result, dated by the competition end date for now."""

    class Kind(models.TextChoices):
        SINGLE = "single", "Single"
        AVERAGE = "average", "Average"

    result_id = models.BigIntegerField()
    kind = models.CharField(max_length=8, choices=Kind.choices)
    attempt_number = models.PositiveSmallIntegerField(null=True, blank=True)
    value = models.IntegerField()
    person_id = models.CharField(max_length=16)
    event_id = models.CharField(max_length=16)
    country_id = models.CharField(max_length=64)
    competition_id = models.CharField(max_length=64)
    # The public v2 export currently omits results.round_id. Keep the provenance
    # slot so it can be populated if the upstream export starts providing it.
    round_id = models.CharField(max_length=64, null=True, blank=True)
    round_type_id = models.CharField(max_length=8)
    achieved_date = models.DateField()

    class Meta:
        db_table = "historical_results"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["single", "average"]),
                name="hist_valid_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(value__gt=0),
                name="hist_positive_value",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="average", attempt_number__isnull=True)
                    | models.Q(
                        kind="single",
                        attempt_number__isnull=False,
                        attempt_number__gte=1,
                        attempt_number__lte=5,
                    )
                ),
                name="hist_kind_attempt_shape",
            ),
            models.UniqueConstraint(
                fields=["result_id", "attempt_number"],
                condition=models.Q(kind="single"),
                name="hist_unique_single_attempt",
            ),
            models.UniqueConstraint(
                fields=["result_id"],
                condition=models.Q(kind="average"),
                name="hist_unique_average",
            ),
        ]
        indexes = [
            models.Index(
                fields=["person_id", "event_id", "kind", "achieved_date"],
                name="hist_person_event_date_idx",
            ),
            models.Index(
                fields=["event_id", "kind", "achieved_date", "value"],
                name="hist_event_kind_date_value_idx",
            ),
        ]


class ProcessedResult(models.Model):
    """Frontend-facing, revision-level classification projection."""

    canonical_result = models.ForeignKey(
        CanonicalResult, on_delete=models.CASCADE, related_name="processed_results"
    )
    canonical_result_revision = models.OneToOneField(
        CanonicalResultRevision,
        on_delete=models.CASCADE,
        related_name="processed_result",
    )
    canonical_revision = models.PositiveIntegerField()
    identity_key = models.CharField(max_length=768)
    wca_competition_id = models.CharField(max_length=64, blank=True, db_index=True)
    competition_name = models.CharField(max_length=255)
    competition_country_code = models.CharField(max_length=8, blank=True)
    competition_start_date = models.DateField(null=True, blank=True)
    competition_end_date = models.DateField(null=True, blank=True)
    competition_timezone = models.CharField(max_length=64, blank=True)
    competition_local_date = models.DateField(null=True, blank=True, db_index=True)
    timezone_resolution_status = models.CharField(max_length=16, default="unresolved")
    timezone_resolution_reason = models.CharField(max_length=64, blank=True)
    round_id = models.CharField(max_length=64, blank=True)
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    round_name = models.CharField(max_length=128, blank=True)
    event_id = models.CharField(max_length=16, db_index=True)
    event_name = models.CharField(max_length=128)
    competitor_name = models.CharField(max_length=255)
    competitor_wca_id = models.CharField(max_length=16, blank=True, db_index=True)
    country_code = models.CharField(max_length=8, blank=True)
    kind = models.CharField(max_length=8, choices=CanonicalResult.Kind.choices)
    value = models.IntegerField()
    formatted_result = models.CharField(max_length=128)
    entered_at = models.DateTimeField(null=True, blank=True)
    first_observed_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    source_url = models.URLField(max_length=512, blank=True)
    canonical_status = models.CharField(max_length=16, choices=CanonicalResult.Status.choices)
    validation_status = models.CharField(
        max_length=16, choices=CanonicalResult.ValidationStatus.choices
    )
    validation_reason = models.CharField(max_length=128, blank=True)
    classification_at = models.DateTimeField(db_index=True)
    classified_at = models.DateTimeField()
    is_valid_result = models.BooleanField(default=True, db_index=True)
    invalidity_reason = models.CharField(max_length=64, blank=True)
    replacement = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replaced_results",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-classification_at", "-canonical_result_id", "-canonical_revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["canonical_result", "canonical_revision"],
                name="unique_processed_result_revision",
            )
        ]
        indexes = [
            models.Index(
                fields=["event_id", "kind", "classification_at"],
                name="processed_result_timeline_idx",
            )
        ]


class ProcessedResultRecordLevel(models.Model):
    class ClassificationOutcome(models.TextChoices):
        NONE = "none", "No record"
        BROKEN = "broken", "Broken"
        TIED = "tied", "Tied"

    class RecognitionStatus(models.TextChoices):
        NOT_APPLICABLE = "not_applicable", "Not applicable"
        RECOGNIZED = "recognized", "Recognized"
        SUPERSEDED_SAME_ROUND = "superseded_same_round", "Superseded in same round"
        SUPERSEDED_SAME_DAY = "superseded_same_day", "Superseded on same day"

    class CeasedHoldingReason(models.TextChoices):
        SELF_LATER_ROUND = "broken_by_self_later_round", "Broken by self in later round"
        OTHER_LATER_ROUND = "broken_by_other_later_round", "Broken by other in later round"
        SELF_OTHER_COMPETITION = (
            "broken_by_self_other_competition",
            "Broken by self at another competition",
        )
        OTHER_OTHER_COMPETITION = (
            "broken_by_other_other_competition",
            "Broken by other at another competition",
        )

    processed_result = models.ForeignKey(
        ProcessedResult, on_delete=models.CASCADE, related_name="record_levels"
    )
    record_level = models.CharField(max_length=2, choices=RecordLevel.choices)
    record_scope = models.CharField(max_length=128, blank=True, db_index=True)
    classification_outcome = models.CharField(
        max_length=16,
        choices=ClassificationOutcome.choices,
        default=ClassificationOutcome.NONE,
    )
    incumbent_value = models.IntegerField(null=True, blank=True)
    recognition_status = models.CharField(
        max_length=32,
        choices=RecognitionStatus.choices,
        default=RecognitionStatus.NOT_APPLICABLE,
    )
    is_shared_tie = models.BooleanField(default=False)
    currently_holds = models.BooleanField(default=False, db_index=True)
    ceased_holding_reason = models.CharField(
        max_length=48, choices=CeasedHoldingReason.choices, blank=True
    )
    ceased_holding_by = models.ForeignKey(
        ProcessedResult,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="record_levels_ceased",
    )
    superseded_by = models.ForeignKey(
        ProcessedResult,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="record_levels_superseded",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["processed_result_id", "record_level"]
        constraints = [
            models.UniqueConstraint(
                fields=["processed_result", "record_level"],
                name="unique_processed_result_record_level",
            )
        ]
        indexes = [
            models.Index(
                fields=["record_level", "record_scope", "classification_outcome"],
                name="processed_level_scope_idx",
            )
        ]


class WCARecordSnapshot(models.Model):
    """A normalized, auditable response from the official WCA records endpoint."""

    source_url = models.URLField(max_length=512)
    payload_hash = models.CharField(max_length=64, unique=True)
    records = models.JSONField(default=dict)
    record_count = models.PositiveIntegerField(default=0)
    first_fetched_at = models.DateTimeField()
    last_fetched_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_fetched_at", "-id"]


class RecordValidation(models.Model):
    """Independent evidence that a result meets one official record benchmark."""

    class Validator(models.TextChoices):
        WCA_RECORDS_API = "wca_records_api", "WCA records API"

    class Status(models.TextChoices):
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    result = models.ForeignKey(
        CanonicalResult, on_delete=models.CASCADE, related_name="record_validations"
    )
    snapshot = models.ForeignKey(
        WCARecordSnapshot, on_delete=models.PROTECT, related_name="validations"
    )
    validator = models.CharField(max_length=32, choices=Validator.choices)
    level = models.CharField(
        max_length=2,
        choices=(
            (RecordLevel.WORLD, "World"),
            (RecordLevel.CONTINENTAL, "Continental"),
            (RecordLevel.NATIONAL, "National"),
        ),
    )
    region_code = models.CharField(max_length=64, blank=True)
    result_value = models.IntegerField()
    benchmark_value = models.IntegerField()
    status = models.CharField(max_length=16, choices=Status.choices)
    reason = models.CharField(max_length=128)
    checked_at = models.DateTimeField()
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["result_id", "level"]
        constraints = [
            models.UniqueConstraint(
                fields=["result", "validator", "level"],
                name="unique_record_validation_per_validator_level",
            )
        ]


class RecentRecordObservation(models.Model):
    """A record as independently detected by one experimental pipeline."""

    class IngestionMethod(models.TextChoices):
        API_POLLING = "api_polling", "API polling"
        GRAPHQL_SUBSCRIPTION = "graphql_subscription", "GraphQL subscription"
        CUBINGCHINA_WEBSOCKET = "cubingchina_websocket", "CubingChina WebSocket"

    class Kind(models.TextChoices):
        SINGLE = "single", "Single"
        AVERAGE = "average", "Average"

    class Level(models.TextChoices):
        WORLD = "WR", "World"
        CONTINENTAL = "CR", "Continental"
        NATIONAL = "NR", "National"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        WITHDRAWN = "withdrawn", "Withdrawn"

    stable_result_identity = models.CharField(max_length=255)
    canonical_key = models.CharField(max_length=512, db_index=True)
    ingestion_method = models.CharField(max_length=32, choices=IngestionMethod.choices)
    source = models.CharField(max_length=32, default="wca_live")
    source_result_id = models.CharField(max_length=255, blank=True)
    source_competition_id = models.CharField(max_length=64, blank=True)
    source_competitor_id = models.CharField(max_length=64, blank=True)
    wca_live_record_id = models.CharField(max_length=255, blank=True)
    wca_live_result_id = models.CharField(max_length=255)
    wca_live_competition_id = models.CharField(max_length=64, blank=True)
    wca_competition_id = models.CharField(max_length=64)
    competition_name = models.CharField(max_length=255)
    competition_country_code = models.CharField(max_length=8, blank=True)
    competition_start_date = models.DateField(null=True, blank=True)
    competition_end_date = models.DateField(null=True, blank=True)
    round_id = models.CharField(max_length=64, blank=True)
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    round_name = models.CharField(max_length=128, blank=True)
    event_id = models.CharField(max_length=16)
    event_name = models.CharField(max_length=128)
    competitor_name = models.CharField(max_length=255)
    competitor_wca_id = models.CharField(max_length=16, blank=True)
    competitor_wca_live_id = models.CharField(max_length=64, blank=True)
    country_code = models.CharField(max_length=8, blank=True)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    raw_result = models.IntegerField()
    formatted_result = models.CharField(max_length=128)
    record_level = models.CharField(max_length=2, choices=Level.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    source_url = models.URLField(max_length=512, blank=True)
    source_update_timestamp = models.DateTimeField(null=True, blank=True)
    first_observed_at = models.DateTimeField()
    detected_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    source_payload = models.JSONField(default=dict)
    canonical_result = models.ForeignKey(
        CanonicalResult,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_record_projections",
    )
    persisted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-detected_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "stable_result_identity",
                    "kind",
                    "record_level",
                    "ingestion_method",
                ],
                name="unique_record_observation_per_pipeline",
            )
        ]


class SubscriptionRound(models.Model):
    class Status(models.TextChoices):
        DISCOVERED = "discovered", "Discovered"
        SUBSCRIBED = "subscribed", "Subscribed"
        ERROR = "error", "Error"
        RETIRED = "retired", "Retired"

    round_id = models.CharField(max_length=64, primary_key=True)
    wca_live_competition_id = models.CharField(max_length=64)
    wca_competition_id = models.CharField(max_length=64)
    competition_name = models.CharField(max_length=255)
    competition_country_code = models.CharField(max_length=8, blank=True)
    competition_timezone = models.CharField(max_length=64, blank=True)
    competition_start_date = models.DateField()
    competition_end_date = models.DateField()
    event_id = models.CharField(max_length=16)
    event_name = models.CharField(max_length=128)
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    round_name = models.CharField(max_length=128, blank=True)
    format_id = models.CharField(max_length=8, blank=True)
    format_sort_by = models.CharField(max_length=16, blank=True)
    expected_attempts = models.PositiveSmallIntegerField(null=True, blank=True)
    cutoff_attempts = models.PositiveSmallIntegerField(null=True, blank=True)
    cutoff_value = models.IntegerField(null=True, blank=True)
    subscription_status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DISCOVERED
    )
    subscription_id = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    last_subscribed_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_processed_snapshot_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    discovered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["competition_start_date", "competition_name", "event_id", "round_number"]


class WCALiveDiffTable(models.Model):
    """Latest normalized source state, used as the restart-safe diff baseline."""

    round = models.ForeignKey(
        SubscriptionRound, on_delete=models.CASCADE, related_name="result_states"
    )
    result_id = models.CharField(max_length=255)
    stable_result_identity = models.CharField(max_length=255)
    competitor_wca_live_id = models.CharField(max_length=64, blank=True)
    competitor_wca_id = models.CharField(max_length=16, blank=True)
    competitor_name = models.CharField(max_length=255)
    country_code = models.CharField(max_length=8, blank=True)
    attempts = models.JSONField(default=list)
    best = models.IntegerField(null=True, blank=True)
    average = models.IntegerField(null=True, blank=True)
    single_record_tag = models.CharField(max_length=8, blank=True)
    average_record_tag = models.CharField(max_length=8, blank=True)
    entered_at = models.DateTimeField(null=True, blank=True)
    meaningful_hash = models.CharField(max_length=64)
    normalized_payload = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    first_observed_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    processed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["round", "result_id"], name="unique_subscription_result_per_round"
            )
        ]


class IngestionWorkerStatus(models.Model):
    ingestion_method = models.CharField(
        max_length=32, choices=RecentRecordObservation.IngestionMethod.choices, unique=True
    )
    is_running = models.BooleanField(default=False)
    connected = models.BooleanField(default=False)
    process_id = models.PositiveIntegerField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_stopped_at = models.DateTimeField(null=True, blank=True)
    last_poll_started_at = models.DateTimeField(null=True, blank=True)
    last_successful_poll_at = models.DateTimeField(null=True, blank=True)
    last_connection_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_successful_discovery_at = models.DateTimeField(null=True, blank=True)
    last_successful_snapshot_at = models.DateTimeField(null=True, blank=True)
    subscribed_round_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CubingChinaCompetitionTarget(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        ERROR = "error", "Error"
        RETIRED = "retired", "Retired"

    slug = models.CharField(max_length=180, unique=True)
    cubingchina_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    wca_competition_id = models.CharField(max_length=64, blank=True)
    competition_name = models.CharField(max_length=255)
    competition_start_date = models.DateField()
    competition_end_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    active = models.BooleanField(default=True)
    connected = models.BooleanField(default=False)
    last_discovered_at = models.DateTimeField(null=True, blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_snapshot_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    websocket_diagnostics = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["competition_start_date", "competition_name"]


class CubingChinaRoundTarget(models.Model):
    competition = models.ForeignKey(
        CubingChinaCompetitionTarget, on_delete=models.CASCADE, related_name="rounds"
    )
    event_id = models.CharField(max_length=16)
    event_name = models.CharField(max_length=128)
    round_id = models.CharField(max_length=16)
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    round_name = models.CharField(max_length=128, blank=True)
    format = models.CharField(max_length=8, blank=True)
    cutoff = models.IntegerField(default=0)
    time_limit = models.IntegerField(default=0)
    source_status = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    last_snapshot_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["competition", "event_id", "round_number", "round_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "event_id", "round_id"],
                name="unique_cubingchina_round_target",
            )
        ]


class CubingChinaDiffTable(models.Model):
    round = models.ForeignKey(
        CubingChinaRoundTarget, on_delete=models.CASCADE, related_name="result_states"
    )
    result_id = models.CharField(max_length=64)
    stable_result_identity = models.CharField(max_length=255)
    competitor_number = models.PositiveIntegerField()
    competitor_name = models.CharField(max_length=255, blank=True)
    competitor_wca_id = models.CharField(max_length=16, blank=True)
    region = models.CharField(max_length=128, blank=True)
    country_code = models.CharField(max_length=8, blank=True)
    attempts = models.JSONField(default=list)
    best = models.IntegerField(null=True, blank=True)
    average = models.IntegerField(null=True, blank=True)
    single_record_tag = models.CharField(max_length=8, blank=True)
    average_record_tag = models.CharField(max_length=8, blank=True)
    meaningful_hash = models.CharField(max_length=64)
    normalized_payload = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    first_observed_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    processed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["round", "result_id"], name="unique_cubingchina_result_per_round"
            )
        ]
