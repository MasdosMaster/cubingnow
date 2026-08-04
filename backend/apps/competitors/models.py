from django.db import models

from apps.competitions.models import Competition


class Competitor(models.Model):
    wca_id = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=255)
    country_code = models.CharField(max_length=2)
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["competitor", "competition"], name="unique_competitor_attendance"
            )
        ]

