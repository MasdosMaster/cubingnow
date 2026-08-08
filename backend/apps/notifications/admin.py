from django.contrib import admin

from .models import (
    NotificationDelivery,
    NotificationEndpoint,
    NotificationEvent,
    NotificationPreference,
)


@admin.register(NotificationEndpoint)
class NotificationEndpointAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider",
        "active",
        "activated_at",
        "user",
        "created_at",
        "last_success_at",
        "last_failure_at",
    )
    list_filter = ("provider", "active")
    readonly_fields = ("id", "created_at", "updated_at")
    exclude = ("management_token_digest",)
    search_fields = ("=id",)


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "enabled", "endpoint", "user", "updated_at")
    list_filter = ("notification_type", "enabled")
    search_fields = ("=endpoint__id", "=user__username")


@admin.register(NotificationEvent)
class NotificationEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "notification_type",
        "achievement",
        "occurred_at",
        "created_at",
        "correction_count",
    )
    list_filter = ("notification_type",)
    search_fields = ("=id", "deduplication_key")
    readonly_fields = ("id", "created_at")


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event",
        "endpoint",
        "provider",
        "status",
        "attempt_count",
        "next_attempt_at",
        "sent_at",
    )
    list_filter = ("provider", "status")
    search_fields = ("=event__id", "=endpoint__id", "last_error_code")
    readonly_fields = ("created_at", "updated_at")
