"""Async client for the StatsHub 'gismo' JSON feed."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import (
    FEED_BASE,
    FEED_HEADERS,
    MAX_CONCURRENCY,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
)

log = logging.getLogger(__name__)


class FeedError(RuntimeError):
    pass


class Feed:
    """Thin wrapper that signs, throttles and retries feed calls."""

    def __init__(self, token: str, concurrency: int = MAX_CONCURRENCY):
        self._token = token
        self._sem = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Feed":
        self._client = httpx.AsyncClient(
            headers=FEED_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=MAX_CONCURRENCY * 2),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def get(self, path: str) -> dict[str, Any]:
        """GET a gismo path (e.g. ``stats_season_fixtures/140756``)."""
        assert self._client is not None, "use Feed as an async context manager"
        url = f"{FEED_BASE}/{path}?T={self._token}"

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            async with self._sem:
                try:
                    response = await self._client.get(url)
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    if response.status_code == 200:
                        try:
                            return response.json()
                        except ValueError as exc:
                            last_error = exc
                    elif response.status_code in (401, 403):
                        raise FeedError(
                            f"token rejected on {path} "
                            f"(HTTP {response.status_code}) — refresh it"
                        )
                    else:
                        last_error = FeedError(
                            f"HTTP {response.status_code} on {path}"
                        )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.6 * attempt)

        raise FeedError(f"{path} failed after {MAX_RETRIES} attempts: {last_error}")

    @staticmethod
    def payload(envelope: dict[str, Any]) -> Any:
        """Unwrap the ``{queryUrl, doc:[{data: ...}]}`` envelope."""
        doc = envelope.get("doc")
        if isinstance(doc, list):
            if not doc:
                return None
            doc = doc[0]
        if not isinstance(doc, dict):
            return None
        return doc.get("data")
