import httpx

from .exceptions import WCALiveIntegrationError


class WCALiveAPIClient:
    def __init__(self, endpoint: str, timeout: float = 30.0):
        self.endpoint = endpoint
        self.timeout = timeout

    def execute(self, query: str, variables: dict | None = None) -> dict:
        response = httpx.post(
            self.endpoint,
            json={"query": query, "variables": variables or {}},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise WCALiveIntegrationError(f"WCA Live GraphQL errors: {payload['errors']}")
        return payload.get("data", {})
