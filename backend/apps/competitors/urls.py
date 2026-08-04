from rest_framework.routers import DefaultRouter

from .views import AttendanceViewSet, CompetitorViewSet

router = DefaultRouter()
router.register("attendance", AttendanceViewSet, basename="attendance")
router.register("", CompetitorViewSet, basename="competitor")
urlpatterns = router.urls

