import base64
import json
import os
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.utils import timezone
from py_vapid import Vapid, VapidException
from pywebpush import WebPushException, webpush
from requests import exceptions as requests_exceptions

from apps.notifications.types import DeliveryOutcome, DeliveryResult
from apps.notifications.validation import validate_subscription_keys


def _retry_at(response) -> datetime | None:
    value = response.headers.get("Retry-After") if response is not None else None
    if not value:
        return None
    try:
        return timezone.now() + timedelta(seconds=max(0, int(value)))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError, OverflowError):
            return None


class WebPushProvider:
    def __init__(self):
        self.private_key = settings.WEB_PUSH_VAPID_PRIVATE_KEY
        self.subject = settings.WEB_PUSH_VAPID_SUBJECT
        if not self.private_key or not self.subject:
            raise ImproperlyConfigured("Web Push VAPID credentials are not configured")
        subject = urlsplit(self.subject)
        if subject.scheme not in {"https", "mailto"}:
            raise ImproperlyConfigured("WEB_PUSH_VAPID_SUBJECT must use https: or mailto:")
        try:
            if os.path.isfile(self.private_key):
                with open(self.private_key, "rb") as private_key_file:
                    private_key_bytes = private_key_file.read()
                vapid = (
                    Vapid.from_pem(private_key_bytes)
                    if b"-----BEGIN" in private_key_bytes
                    else Vapid.from_der(private_key_bytes)
                )
            else:
                vapid = Vapid.from_string(self.private_key)
            self.vapid = vapid
            derived_public_key = (
                base64.urlsafe_b64encode(
                    vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
                )
                .rstrip(b"=")
                .decode("ascii")
            )
        except (OSError, TypeError, ValueError, VapidException) as exc:
            raise ImproperlyConfigured(
                "WEB_PUSH_VAPID_PRIVATE_KEY is not a valid VAPID private key"
            ) from exc
        if (
            settings.WEB_PUSH_VAPID_PUBLIC_KEY
            and settings.WEB_PUSH_VAPID_PUBLIC_KEY != derived_public_key
        ):
            raise ImproperlyConfigured("Configured VAPID public and private keys do not match")

    def send(self, *, endpoint, payload) -> DeliveryResult:
        try:
            subscription = endpoint.webpush_subscription
        except ObjectDoesNotExist:
            return DeliveryResult(
                DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE,
                "invalid_subscription",
                "Web Push subscription data is missing",
            )

        subscription_info = {
            "endpoint": subscription.endpoint_url,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        }
        if (
            not subscription.endpoint_url.startswith("https://")
            or len(subscription.endpoint_url) > 2048
        ):
            return DeliveryResult(
                DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE,
                "invalid_subscription",
                "Push endpoint data is invalid",
            )
        try:
            validate_subscription_keys(subscription.p256dh, subscription.auth)
        except ValueError:
            return DeliveryResult(
                DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE,
                "invalid_subscription",
                "Push endpoint data is invalid",
            )
        try:
            response = webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload, separators=(",", ":")),
                vapid_private_key=self.vapid,
                vapid_claims={"sub": self.subject},
                ttl=settings.WEB_PUSH_TTL_SECONDS,
                timeout=settings.WEB_PUSH_REQUEST_TIMEOUT_SECONDS,
            )
            message_id = response.headers.get("Location") if response is not None else None
            return DeliveryResult(
                DeliveryOutcome.SUCCESS,
                provider_message_id=message_id,
            )
        except WebPushException as exc:
            response = exc.response
            status = response.status_code if response is not None else None
            if status in (404, 410):
                return DeliveryResult(
                    DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE,
                    f"http_{status}",
                    "Push endpoint is no longer valid",
                )
            if status == 429:
                return DeliveryResult(
                    DeliveryOutcome.TRANSIENT_FAILURE,
                    "http_429",
                    "Push service rate limited the request",
                    retry_at=_retry_at(response),
                )
            if status is not None and 500 <= status < 600:
                return DeliveryResult(
                    DeliveryOutcome.TRANSIENT_FAILURE,
                    f"http_{status}",
                    "Push service temporarily failed",
                )
            if status in (401, 403, 413):
                return DeliveryResult(
                    DeliveryOutcome.APPLICATION_FAILURE,
                    f"http_{status}",
                    "Push service rejected the application credentials or payload",
                )
            if status is not None and 400 <= status < 500:
                return DeliveryResult(
                    DeliveryOutcome.PERMANENT_ENDPOINT_FAILURE,
                    f"http_{status}",
                    "Push endpoint data was rejected",
                )
            return DeliveryResult(
                DeliveryOutcome.APPLICATION_FAILURE,
                "webpush_configuration",
                "Web Push could not construct an authenticated request",
            )
        except (requests_exceptions.Timeout, requests_exceptions.ConnectionError):
            return DeliveryResult(
                DeliveryOutcome.TRANSIENT_FAILURE,
                "network_temporary",
                "Temporary network failure contacting push service",
            )
        except (ValueError, TypeError):
            return DeliveryResult(
                DeliveryOutcome.APPLICATION_FAILURE,
                "webpush_configuration",
                "Web Push configuration or subscription data is invalid",
            )
