import time

import httpx


class CubingChinaScraperClient:
    """HTTP client for public CubingChina pages.

    HTML parsing remains in page-specific modules. This client owns only shared
    HTTP behavior.
    """

    def __init__(
        self,
        base_url: str = "https://cubing.com",
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

    def get_page(self, path: str, **params) -> str:
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
                return response.text
            except httpx.TransportError:
                self._last_request_at = time.monotonic()
                if attempt >= self._retry_attempts:
                    raise
                time.sleep(2 ** (attempt - 1))
        raise RuntimeError("CubingChina request retries were exhausted")

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
