from django.conf import settings
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .authentication import CsrfProtectedSessionAuthentication
from .models import (
    NotificationDelivery,
    NotificationEndpoint,
    NotificationProvider,
    NotificationType,
    WebPushSubscription,
)
from .policies import DEFAULT_PREFERENCES, may_enable_notification
from .providers import push_provider_is_configured
from .serializers import (
    PreferenceUpdateSerializer,
    SubscriptionDeleteSerializer,
    SubscriptionRegistrationSerializer,
)
from .services import set_endpoint_preferences


def _public_endpoint(endpoint, management_token=None):
    payload = {
        "endpoint_id": str(endpoint.id),
        "active": endpoint.active,
        "preferences": {
            preference.notification_type: preference.enabled
            for preference in endpoint.preferences.all()
        },
    }
    for notification_type, default in DEFAULT_PREFERENCES.items():
        payload["preferences"].setdefault(notification_type, default)
    if management_token:
        payload["management_token"] = management_token
    return payload


@method_decorator(ensure_csrf_cookie, name="dispatch")
class NotificationConfigView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "notifications"

    def get(self, request):
        configured = push_provider_is_configured()
        return Response(
            {
                "vapid_public_key": settings.WEB_PUSH_VAPID_PUBLIC_KEY,
                "supported_notification_types": [
                    {
                        "value": value,
                        "label": label,
                        "default": DEFAULT_PREFERENCES[value],
                    }
                    for value, label in NotificationType.choices
                ],
                "web_push_configured": configured,
                "csrf_token": get_token(request),
            }
        )


class CsrfMutationAPIView(APIView):
    authentication_classes = [CsrfProtectedSessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "notifications"


class SubscriptionView(CsrfMutationAPIView):
    @transaction.atomic
    def post(self, request):
        serializer = SubscriptionRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        subscription_data = data["subscription"]
        key_data = subscription_data["keys"]
        subscription = (
            WebPushSubscription.objects.select_for_update()
            .select_related("endpoint")
            .filter(endpoint_url=subscription_data["endpoint"])
            .first()
        )
        supplied_token = data.get("management_token", "")
        created = False
        if subscription is None:
            endpoint = NotificationEndpoint(provider=NotificationProvider.WEBPUSH)
            management_token = endpoint.issue_management_token()
            endpoint.save()
            try:
                with transaction.atomic():
                    subscription = WebPushSubscription.objects.create(
                        endpoint=endpoint,
                        endpoint_url=subscription_data["endpoint"],
                        p256dh=key_data["p256dh"],
                        auth=key_data["auth"],
                        expiration_time=subscription_data.get("expirationTime"),
                    )
                created = True
            except IntegrityError:
                endpoint.delete()
                subscription = (
                    WebPushSubscription.objects.select_for_update()
                    .select_related("endpoint")
                    .get(endpoint_url=subscription_data["endpoint"])
                )

        if not created:
            endpoint = subscription.endpoint
            if endpoint.accepts_management_token(supplied_token):
                management_token = supplied_token
            else:
                management_token = endpoint.issue_management_token()
            update_fields = [
                "management_token_digest",
                "active",
                "deactivated_at",
                "deactivation_reason",
                "updated_at",
            ]
            if not endpoint.active:
                endpoint.activated_at = timezone.now()
                update_fields.append("activated_at")
            endpoint.active = True
            endpoint.deactivated_at = None
            endpoint.deactivation_reason = ""
            endpoint.save(update_fields=update_fields)

        requested_preferences = data["preferences"]
        for notification_type, enabled in requested_preferences.items():
            if enabled and not may_enable_notification(endpoint, notification_type):
                return Response(
                    {"detail": f"{notification_type} cannot be enabled"},
                    status=status.HTTP_403_FORBIDDEN,
                )
        if not created:
            subscription.p256dh = key_data["p256dh"]
            subscription.auth = key_data["auth"]
            subscription.expiration_time = subscription_data.get("expirationTime")
            subscription.save(update_fields=["p256dh", "auth", "expiration_time", "updated_at"])
        set_endpoint_preferences(endpoint, requested_preferences)
        endpoint.refresh_from_db()
        return Response(
            _public_endpoint(endpoint, management_token),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @transaction.atomic
    def delete(self, request):
        serializer = SubscriptionDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint = (
            NotificationEndpoint.objects.select_for_update()
            .filter(pk=serializer.validated_data["endpoint_id"])
            .first()
        )
        if not endpoint or not endpoint.accepts_management_token(
            serializer.validated_data["management_token"]
        ):
            return Response(
                {"detail": "Subscription management credentials are invalid"},
                status=status.HTTP_404_NOT_FOUND,
            )
        now = timezone.now()
        endpoint.active = False
        endpoint.deactivated_at = now
        endpoint.deactivation_reason = "guest_disabled"
        endpoint.save(
            update_fields=[
                "active",
                "deactivated_at",
                "deactivation_reason",
                "updated_at",
            ]
        )
        NotificationDelivery.objects.filter(endpoint=endpoint).filter(
            status__in=[
                NotificationDelivery.Status.PENDING,
                NotificationDelivery.Status.RETRY,
            ]
        ).update(
            status=NotificationDelivery.Status.CANCELLED,
            next_attempt_at=None,
            last_error_code="guest_disabled",
            last_error_message="Guest disabled notifications",
            updated_at=now,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PreferenceView(CsrfMutationAPIView):
    @transaction.atomic
    def patch(self, request):
        serializer = PreferenceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint = (
            NotificationEndpoint.objects.select_for_update()
            .filter(pk=serializer.validated_data["endpoint_id"], active=True)
            .first()
        )
        if not endpoint or not endpoint.accepts_management_token(
            serializer.validated_data["management_token"]
        ):
            return Response(
                {"detail": "Subscription management credentials are invalid"},
                status=status.HTTP_404_NOT_FOUND,
            )
        for notification_type, enabled in serializer.validated_data["preferences"].items():
            if enabled and not may_enable_notification(endpoint, notification_type):
                return Response(
                    {"detail": f"{notification_type} cannot be enabled"},
                    status=status.HTTP_403_FORBIDDEN,
                )
        set_endpoint_preferences(endpoint, serializer.validated_data["preferences"])
        endpoint.refresh_from_db()
        return Response(_public_endpoint(endpoint))
