from rest_framework.routers import DefaultRouter

from .views import CompetitionViewSet

router = DefaultRouter()
router.register("", CompetitionViewSet, basename="competition")
urlpatterns = router.urls

