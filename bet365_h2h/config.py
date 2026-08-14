"""Shared configuration for the StatsHub feed."""

from pathlib import Path

# bet365's stats provider. The percentages shown inside bet365's "Statistics"
# panel are served from here, not from bet365 itself.
STATSHUB_URL = "https://statshub.sportradar.com/bet365de/en/match/72221158"
FEED_HOST = "https://sh.fn.sportradar.com"
CLIENT = "bet365de"
LANG = "en"
TZ = "Etc:UTC"

FEED_BASE = f"{FEED_HOST}/{CLIENT}/{LANG}/{TZ}/gismo"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

FEED_HEADERS = {
    "Origin": "https://statshub.sportradar.com",
    "Referer": "https://statshub.sportradar.com/",
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
}

# Sport ids as they appear in the feed's competition tree. The tree endpoint
# returns every sport in one payload; these are the ones the tool exposes.
SOCCER_SID = 1
BASKETBALL_SID = 2

SPORTS = {
    SOCCER_SID: "Football",
    BASKETBALL_SID: "Basketball",
}
DEFAULT_SPORT = SOCCER_SID

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
OUTPUT_DIR = ROOT / "output"
TOKEN_FILE = CACHE_DIR / "token.json"

# Politeness / throughput knobs.
MAX_CONCURRENCY = 8
REQUEST_TIMEOUT = 40.0
MAX_RETRIES = 3
