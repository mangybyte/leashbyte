"""Pluggable search adapters."""

from abc import ABC, abstractmethod
from urllib.parse import urlsplit

import httpx

from .errors import LeashByteError
from .models import SearchResult


class SearchBackend(ABC):
    @abstractmethod
    async def search(
        self,
        query: str,
        limit: int,
        language: str,
        safe_search: int,
    ) -> list[SearchResult]:
        """Return stable search results."""

    @abstractmethod
    async def ready(self) -> bool:
        """Return whether the search service answers."""


class SearXNGBackend(SearchBackend):
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        parts = urlsplit(self.base_url)
        self.host = parts.hostname or "search service"
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise RuntimeError("LEASHBYTE_SEARXNG_BASE_URL must be an HTTP or HTTPS URL.")

    async def search(
        self,
        query: str,
        limit: int,
        language: str,
        safe_search: int,
    ) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                timeout=self.timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{self.base_url}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "language": language,
                        "safesearch": safe_search,
                    },
                )
        except httpx.HTTPError as error:
            raise LeashByteError(
                502,
                "search_unavailable",
                f"The search host {self.host} did not complete the request.",
            ) from error
        if response.status_code != 200:
            raise LeashByteError(
                502,
                "search_unavailable",
                f"The search host {self.host} returned HTTP {response.status_code}.",
            )
        try:
            payload = response.json()
            raw_results = payload["results"]
            if not isinstance(raw_results, list):
                raise TypeError("results is not a list")
            results = [
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["url"]),
                    snippet=str(item.get("content") or ""),
                )
                for item in raw_results
                if isinstance(item, dict) and item.get("url")
            ]
        except (ValueError, KeyError, TypeError) as error:
            raise LeashByteError(
                502,
                "invalid_search_response",
                f"The search host {self.host} returned an invalid response.",
            ) from error
        return results[:limit]

    async def ready(self) -> bool:
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                timeout=self.timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(f"{self.base_url}/")
            return response.status_code == 200
        except httpx.HTTPError:
            return False
