import time

import httpx


class WCAScraperClient:
    """HTTP client for public pages on the main WCA website.

    HTML parsing belongs in a separate parser so transport behavior can be reused
    by records, registrations, and other collectors.
    """

    def __init__(
        self,
        base_url: str = "https://www.worldcubeassociation.org",
        timeout: float = 30.0,
        min_request_interval: float = 0.2,
        retry_attempts: int = 3,
    ):
        self._min_request_interval = max(min_request_interval, 0)
        self._retry_attempts = max(retry_attempts, 1)
        self._last_request_at = 0.0
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "CubingNow/0.1 (+https://cubingnow.com)"},
        )

    def _get(self, path: str, params):
        for attempt in range(1, self._retry_attempts + 1):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)
            try:
                response = self._client.get(path, params=params)
                self._last_request_at = time.monotonic()
                if (
                    response.status_code == 429 or response.status_code >= 500
                ) and attempt < self._retry_attempts:
                    retry_after = response.headers.get("Retry-After", "")
                    delay = float(retry_after) if retry_after.isdigit() else 2 ** (attempt - 1)
                    time.sleep(min(delay, 30))
                    continue
                response.raise_for_status()
                return response
            except httpx.TransportError:
                self._last_request_at = time.monotonic()
                if attempt >= self._retry_attempts:
                    raise
                time.sleep(2 ** (attempt - 1))
        raise RuntimeError("WCA request retries were exhausted")

    def get_page(self, path: str, **params) -> str:
        return self._get(path, params).text

    def get_json(self, path: str, **params):
        return self._get(path, params).json()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
