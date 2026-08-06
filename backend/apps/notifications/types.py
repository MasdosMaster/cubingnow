from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from .models import NotificationEndpoint


class DeliveryOutcome(StrEnum):
    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_ENDPOINT_FAILURE = "permanent_endpoint_failure"
    APPLICATION_FAILURE = "application_failure"


@dataclass(frozen=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    error_code: str = ""
    error_message: str = ""
    retry_at: datetime | None = None
    provider_message_id: str | None = None


class PushProvider(Protocol):
    def send(
        self,
        *,
        endpoint: NotificationEndpoint,
        payload: dict[str, Any],
    ) -> DeliveryResult: ...
