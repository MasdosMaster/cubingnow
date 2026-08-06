import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class NotificationType(models.TextChoices):
    RECORD_WR = "record_wr", "World Records"
    RECORD_CR = "record_cr", "Continental Records"
    RECORD_NR = "record_nr", "National Records"


class NotificationProvider(models.TextChoices):
    WEBPUSH = "webpush", "Web Push"


class NotificationEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(
        max_length=32,
        choices=NotificationProvider.choices,
        default=NotificationProvider.WEBPUSH,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_endpoints",
    )
    management_token_digest = models.CharField(max_length=64, editable=False)
    active = models.BooleanField(default=True, db_index=True)
    activated_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    consecutive_failure_count = models.PositiveIntegerField(default=0)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivation_reason = models.CharField(max_length=64, blank=True)

    def __str__(self):
        return f"{self.provider}:{self.id}"

    @staticmethod
    def digest_management_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue_management_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.management_token_digest = self.digest_management_token(token)
        return token

    def accepts_management_token(self, token: str) -> bool:
        if not token or not self.management_token_digest:
            return False
        return secrets.compare_digest(
            self.management_token_digest,
            self.digest_management_token(token),
        )


class WebPushSubscription(models.Model):
    endpoint = models.OneToOneField(
        NotificationEndpoint,
        on_delete=models.CASCADE,
        related_name="webpush_subscription",
    )
    endpoint_url = models.TextField(unique=True)
    p256dh = models.TextField()
    auth = models.TextField()
    expiration_time = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Web Push subscription for {self.endpoint_id}"


class NotificationPreference(models.Model):
    endpoint = models.ForeignKey(
        NotificationEndpoint,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="preferences",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    notification_type = models.CharField(max_length=64, choices=NotificationType.choices)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(endpoint__isnull=False, user__isnull=True)
                    | Q(endpoint__isnull=True, user__isnull=False)
                ),
                name="notification_preference_exactly_one_owner",
            ),
            models.UniqueConstraint(
                fields=["endpoint", "notification_type"],
                condition=Q(endpoint__isnull=False),
                name="unique_endpoint_notification_preference",
            ),
            models.UniqueConstraint(
                fields=["user", "notification_type"],
                condition=Q(user__isnull=False),
                name="unique_user_notification_preference",
            ),
        ]


class NotificationEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification_type = models.CharField(max_length=64, choices=NotificationType.choices)
    deduplication_key = models.CharField(max_length=768, unique=True)
    payload = models.JSONField(default=dict)
    target_url = models.CharField(max_length=512)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    source_record = models.ForeignKey(
        "records.RecentRecordObservation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_events",
    )
    correction_count = models.PositiveIntegerField(default=0)
    last_correction_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class NotificationDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        RETRY = "retry", "Retry"
        SENT = "sent", "Sent"
        PERMANENTLY_FAILED = "permanently_failed", "Permanently failed"
        CANCELLED = "cancelled", "Cancelled"

    event = models.ForeignKey(
        NotificationEvent,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    endpoint = models.ForeignKey(
        NotificationEndpoint,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    provider = models.CharField(max_length=32, choices=NotificationProvider.choices)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.CharField(max_length=128, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.CharField(max_length=255, blank=True)
    provider_message_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "endpoint"],
                name="unique_notification_delivery_per_endpoint",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "next_attempt_at"],
                name="notification_due_idx",
            )
        ]
