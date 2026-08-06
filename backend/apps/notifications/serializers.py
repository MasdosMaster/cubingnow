from datetime import UTC, datetime

from rest_framework import serializers

from .policies import DEFAULT_PREFERENCES, SUPPORTED_NOTIFICATION_TYPES
from .validation import validate_subscription_keys

MAX_ENDPOINT_URL_LENGTH = 2048
MAX_P256DH_LENGTH = 512
MAX_AUTH_LENGTH = 256


def validate_preferences(value, *, partial=False):
    if not isinstance(value, dict):
        raise serializers.ValidationError("Preferences must be an object")
    unknown = set(value) - set(SUPPORTED_NOTIFICATION_TYPES)
    if unknown:
        raise serializers.ValidationError(f"Unknown notification type: {min(unknown)}")
    if not partial and not value:
        return dict(DEFAULT_PREFERENCES)
    if any(type(enabled) is not bool for enabled in value.values()):
        raise serializers.ValidationError("Preference values must be Boolean")
    result = dict(value)
    if not partial:
        for notification_type, default in DEFAULT_PREFERENCES.items():
            result.setdefault(notification_type, default)
    return result


class BrowserSubscriptionSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=MAX_ENDPOINT_URL_LENGTH)
    expirationTime = serializers.JSONField(required=False, allow_null=True)
    keys = serializers.DictField()

    def validate_endpoint(self, value):
        if not value.startswith("https://"):
            raise serializers.ValidationError("Push endpoint must use HTTPS")
        return value

    def validate_keys(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Subscription keys must be an object")
        p256dh = value.get("p256dh")
        auth = value.get("auth")
        if not isinstance(p256dh, str) or not p256dh:
            raise serializers.ValidationError("p256dh is required")
        if not isinstance(auth, str) or not auth:
            raise serializers.ValidationError("auth is required")
        if len(p256dh) > MAX_P256DH_LENGTH:
            raise serializers.ValidationError("p256dh is too long")
        if len(auth) > MAX_AUTH_LENGTH:
            raise serializers.ValidationError("auth is too long")
        try:
            validate_subscription_keys(p256dh, auth)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return {"p256dh": p256dh, "auth": auth}

    def validate_expirationTime(self, value):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise serializers.ValidationError("expirationTime must be epoch milliseconds or null")
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (ValueError, OverflowError, OSError) as exc:
            raise serializers.ValidationError("expirationTime is invalid") from exc


class SubscriptionRegistrationSerializer(serializers.Serializer):
    subscription = BrowserSubscriptionSerializer()
    preferences = serializers.JSONField(required=False, default=dict)
    management_token = serializers.CharField(required=False, allow_blank=True, max_length=128)

    def validate_preferences(self, value):
        return validate_preferences(value)


class PreferenceUpdateSerializer(serializers.Serializer):
    endpoint_id = serializers.UUIDField()
    management_token = serializers.CharField(max_length=128)
    preferences = serializers.JSONField()

    def validate_preferences(self, value):
        value = validate_preferences(value, partial=True)
        if not value:
            raise serializers.ValidationError("At least one preference is required")
        return value


class SubscriptionDeleteSerializer(serializers.Serializer):
    endpoint_id = serializers.UUIDField()
    management_token = serializers.CharField(max_length=128)
