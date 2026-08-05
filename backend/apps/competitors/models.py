from django.db import models

from apps.competitions.models import Competition


class Competitor(models.Model):
    class Continent(models.TextChoices):
        AFRICA = "Africa", "Africa"
        ASIA = "Asia", "Asia"
        EUROPE = "Europe", "Europe"
        NORTH_AMERICA = "North America", "North America"
        SOUTH_AMERICA = "South America", "South America"
        OCEANIA = "Oceania", "Oceania"

    wca_id = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=255)
    country_code = models.CharField(max_length=2)
    continent = models.CharField(max_length=20, choices=Continent.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.wca_id})"


class FeaturedCompetitor(models.Model):
    competitor = models.OneToOneField(
        Competitor, on_delete=models.CASCADE, related_name="featured_profile"
    )
    is_active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Attendance(models.Model):
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name="attendances")
    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="attendances"
    )
    observed_at = models.DateTimeField()
    is_accepted = models.BooleanField(default=True)
    sources = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["competitor", "competition"], name="unique_competitor_attendance"
            )
        ]


class AttendanceSyncRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    window_start = models.DateField()
    window_end = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
