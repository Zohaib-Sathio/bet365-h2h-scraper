"""Fetch and cache the signed access token required by the StatsHub feed.

Every feed request must carry a ``T=exp=...~acl=/*~data=...~hmac=...`` token.
It is server-signed with an origin check, covers the whole feed (``acl=/*``)
and lasts roughly 24 hours.

The token is embedded in the server-rendered StatsHub page under the key
``fishnetToken``, so a single plain HTTP GET is enough to obtain it — no
browser is involved anywhere in this tool.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import (
    CACHE_DIR,
    REQUEST_TIMEOUT,
    STATSHUB_URL,
    TOKEN_FILE,
    USER_AGENT,
)

log = logging.getLogger(__name__)

# Refresh the token if it expires within this many seconds.
EXPIRY_MARGIN = 30 * 60

# The token as it appears in the SSR payload, JSON-escaped:
#   \"fishnetToken\",\"exp=...~acl=/*~data=...~hmac=...\"
TOKEN_RE = re.compile(r'exp=\d+~acl=\S+?~hmac=[0-9a-f]{32,}')

PAGE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class Token:
    value: str
    expires_at: int

    @property
    def seconds_left(self) -> int:
        return int(self.expires_at - time.time())

    def is_usable(self) -> bool:
        return self.seconds_left > EXPIRY_MARGIN


def _parse_expiry(token: str) -> int:
    """Pull the ``exp=<unix-ts>`` field out of the token payload."""
    for part in token.split("~"):
        if part.startswith("exp="):
            return int(part[4:])
    raise ValueError(f"no exp field in token: {token[:60]}...")


def _load_cached() -> Token | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        raw = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        token = Token(raw["value"], int(raw["expires_at"]))
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None
    return token if token.is_usable() else None


def _save(token: Token) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(
        json.dumps({"value": token.value, "expires_at": token.expires_at}),
        encoding="utf-8",
    )


def _mint() -> Token:
    """Scrape a fresh token out of the server-rendered StatsHub page."""
    response = httpx.get(
        STATSHUB_URL,
        headers=PAGE_HEADERS,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()

    match = TOKEN_RE.search(response.text)
    if not match:
        raise RuntimeError(
            "no feed token found in the StatsHub page "
            f"({len(response.text)} bytes) — the page layout may have changed"
        )

    value = match.group(0)
    return Token(value, _parse_expiry(value))


def get_token(force_refresh: bool = False) -> Token:
    if not force_refresh:
        cached = _load_cached()
        if cached:
            log.info("reusing cached token (%.1f h left)", cached.seconds_left / 3600)
            return cached

    log.info("fetching a fresh token from StatsHub...")
    token = _mint()
    _save(token)
    log.info("token acquired, valid for %.1f h", token.seconds_left / 3600)
    return token
