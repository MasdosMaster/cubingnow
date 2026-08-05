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
    competition_start_date = models.DateField()
    competition_end_date = models.DateField()
    event_id = models.CharField(max_length=16)
    event_name = models.CharField(max_length=128)
    round_number = models.PositiveSmallIntegerField(null=True, blank=True)
    round_name = models.CharField(max_length=128, blank=True)
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


class SubscriptionResultState(models.Model):
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


class CubingChinaResultState(models.Model):
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
