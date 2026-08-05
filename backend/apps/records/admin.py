from django.contrib import admin

from .models import (
    CubingChinaCompetitionTarget,
    CubingChinaResultState,
    CubingChinaRoundTarget,
    IngestionRun,
    IngestionWorkerStatus,
    RecentRecordObservation,
    Record,
    Result,
    SourceObservation,
    SubscriptionResultState,
    SubscriptionRound,
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
