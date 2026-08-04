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
        SUBSCRIPTION = "subscription", "Subscription"
        RECONCILIATION = "reconciliation", "Reconciliation"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    mode = models.CharField(max_length=16, choices=Mode.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    observations_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)


class SourceObservation(models.Model):
    source = models.CharField(max_length=32, default="wca_live")
    external_id = models.CharField(max_length=255, blank=True)
    event_type = models.CharField(max_length=128)
    observed_at = models.DateTimeField()
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64, unique=True)
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.SET_NULL, null=True, related_name="observations"
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-observed_at"]

