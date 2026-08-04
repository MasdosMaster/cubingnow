from django.contrib import admin

from .models import Competition


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ("wca_id", "name", "start_date", "country_code")
    search_fields = ("wca_id", "name", "city")

