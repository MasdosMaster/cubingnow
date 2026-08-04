class WCAIntegrationError(Exception):
    """Base error raised by the WCA integration boundary."""


class WCAConfigurationError(WCAIntegrationError):
    """Raised when required endpoint or query configuration is missing."""


class WCAPayloadError(WCAIntegrationError):
    """Raised when an external payload cannot be mapped safely."""

