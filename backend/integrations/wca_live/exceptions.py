class WCALiveIntegrationError(Exception):
    """Base error raised by the WCA Live integration boundary."""


class WCALivePayloadError(WCALiveIntegrationError):
    """Raised when a WCA Live payload cannot be mapped safely."""
