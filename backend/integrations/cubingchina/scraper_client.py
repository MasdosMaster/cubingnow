import httpx


class CubingChinaScraperClient:
    """HTTP client for public CubingChina pages.

    Page-specific parsers and methods will be added as their page contracts are
    investigated. This client owns only shared HTTP behavior.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "CubingNow/0.1 (+https://cubingnow.com)"},
        )

    def get_page(self, path: str, **params) -> str:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return response.text

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
