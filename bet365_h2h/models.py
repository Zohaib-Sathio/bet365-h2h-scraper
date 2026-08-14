"""Plain data holders passed between the scrape stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


# One StatsHub match page per fixture — the exact page bet365 embeds, so any
# row in the output can be checked against the source in one click.
STATSHUB_MATCH_URL = "https://statshub.sportradar.com/bet365de/en/match/{match_id}"


@dataclass(frozen=True)
class League:
    country: str
    name: str
    tournament_id: int
    season_id: int
    level_order: int
    continent: str = "International"
    sport: str = "Football"

    @property
    def label(self) -> str:
        if self.country in ("International", "International Clubs"):
            return self.name
        return f"{self.country} — {self.name}"


@dataclass(frozen=True)
class Match:
    match_id: int
    league: League
    home_name: str
    away_name: str
    home_uid: int
    away_uid: int
    kickoff_uts: int

    @property
    def kickoff(self) -> datetime:
        return datetime.fromtimestamp(self.kickoff_uts, tz=timezone.utc)

    @property
    def fixture(self) -> str:
        return f"{self.home_name} vs {self.away_name}"

    @property
    def stats_url(self) -> str:
        """bet365's own statistics page for this fixture."""
        return STATSHUB_MATCH_URL.format(match_id=self.match_id)


@dataclass(frozen=True)
class H2HRow:
    match: Match
    total_meetings: int
    home_wins: int
    draws: int
    away_wins: int
    home_pct: int
    draw_pct: int
    away_pct: int
