from django.db import models


class Competition(models.Model):
    wca_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    country_code = models.CharField(max_length=2)
    city = models.CharField(max_length=128, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    timezone = models.CharField(max_length=64, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name"]

    def __str__(self):
        return self.name

