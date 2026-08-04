from django.contrib import admin

from .models import Attendance, Competitor, FeaturedCompetitor

admin.site.register(Competitor)
admin.site.register(FeaturedCompetitor)
admin.site.register(Attendance)

