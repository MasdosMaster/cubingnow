from django.contrib import admin

from .models import IngestionRun, Record, Result, SourceObservation

admin.site.register(Result)
admin.site.register(Record)
admin.site.register(IngestionRun)
admin.site.register(SourceObservation)

