from django.contrib import admin

from .models import Attendance, AttendanceSyncRun, Competitor, FeaturedCompetitor

admin.site.register(Competitor)
admin.site.register(FeaturedCompetitor)
admin.site.register(Attendance)
admin.site.register(AttendanceSyncRun)
