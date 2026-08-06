from django.urls import path

from .views import NotificationConfigView, PreferenceView, SubscriptionView

urlpatterns = [
    path("config/", NotificationConfigView.as_view(), name="notification-config"),
    path("subscriptions/", SubscriptionView.as_view(), name="notification-subscriptions"),
    path("preferences/", PreferenceView.as_view(), name="notification-preferences"),
]
