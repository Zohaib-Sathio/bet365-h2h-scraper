"""Scrape pipeline: leagues -> upcoming fixtures -> H2H percentages."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Iterable

from .config import DEFAULT_SPORT, SPORTS
from .feed import Feed, FeedError
from .models import H2HRow, League, Match

log = logging.getLogger(__name__)


def _pct(part: int, whole: int) -> int:
    """Round half-up, matching the widget's Math.round behaviour."""
    return int(math.floor((part / whole) * 100 + 0.5))


async def _tree(feed: Feed) -> list[dict]:
    """The full competition tree for every sport.

    ``config_tree/1`` is not "sport 1" — the path segment is a portal config
    id, and id 1 resolves to a cut-down tree that only carries competitions
    with something happening right now. Overnight, when no football is being
    played, football disappears from it entirely. The response does however
    carry the real config id in ``_configid``; requesting the tree under that
    id returns the complete portal tree at any hour, which is what the filters
    and the fixture sweep are built from.
    """
    envelope = await feed.get("config_tree/1")
    doc = envelope.get("doc") or []
    config_id = (doc[0].get("_configid") if doc else None) or None

    if config_id:
        try:
            full = Feed.payload(await feed.get(f"config_tree/{config_id}"))
            if full:
                return full if isinstance(full, list) else [full]
        except FeedError as exc:
            log.warning("full tree (config %s) failed, falling back: %s", config_id, exc)

    data = Feed.payload(envelope)
    if not data:
        raise FeedError("empty competition tree")
    return data if isinstance(data, list) else [data]


async def load_categories(
    feed: Feed, sport_id: int = DEFAULT_SPORT
) -> list[tuple[int, str, str]]:
    """Every country / region bet365 lists, as (rcid, country, continent).

    The tree covers all sports in one payload, so the sport is selected by
    filtering on ``_sid`` rather than by changing the path.
    """
    sports = await _tree(feed)

    out: dict[int, tuple[str, str]] = {}
    for sport in sports:
        if sport.get("_sid") != sport_id:
            continue
        for category in sport.get("realcategories") or []:
            rcid = category.get("_rcid") or category.get("_id")
            if not rcid:
                continue
            # International / youth / simulated categories carry no country
            # code, so they get their own bucket in the continent filter.
            continent = ((category.get("cc") or {}).get("continent")) or "International"
            out[int(rcid)] = (category.get("name") or "Unknown", continent)

    if not out:
        raise FeedError(f"competition tree carried no categories for sport {sport_id}")

    return sorted(
        ((rcid, name, continent) for rcid, (name, continent) in out.items()),
        key=lambda row: (row[2], row[1]),
    )


def _collect(
    country: str,
    continent: str,
    sport: str,
    tournaments: Any,
    into: dict[int, League],
) -> None:
    for tournament in tournaments or []:
        if not isinstance(tournament, dict):
            continue
        season_id = tournament.get("currentseason") or tournament.get("seasonid")
        if not season_id or tournament.get("outdated"):
            continue
        # The feed carries internal placeholder competitions ("Dummy NBA
        # Divisions" and friends) that never hold real fixtures.
        if str(tournament.get("name", "")).startswith("Dummy"):
            continue
        # Several tournament rows (regular season, playoffs, ...) share one
        # season feed; keep a single entry per season.
        if int(season_id) in into:
            continue
        into[int(season_id)] = League(
            country=country,
            continent=continent,
            sport=sport,
            name=tournament.get("name") or "Unknown",
            tournament_id=int(tournament.get("_id") or 0),
            season_id=int(season_id),
            level_order=int(tournament.get("tournamentlevelorder") or 9999),
        )


async def load_leagues(feed: Feed, sport_id: int = DEFAULT_SPORT) -> list[League]:
    """Enumerate every competition bet365 exposes, with its current season.

    ``config_tree`` alone only lists competitions that are mid-season right
    now, so a league whose first fixture is still a week away (the Premier
    League in mid-August, for example) is missing from it. The per-country
    ``config_tournaments`` feed carries the complete list, so the tree is used
    only to enumerate countries and each country is then expanded.
    """
    sport = SPORTS.get(sport_id, str(sport_id))
    categories = await load_categories(feed, sport_id)
    log.info("%s tree: %d countries/regions", sport, len(categories))

    async def one(rcid: int, country: str, continent: str) -> tuple[str, str, Any]:
        try:
            data = Feed.payload(await feed.get(f"config_tournaments/1/{rcid}"))
        except FeedError as exc:
            log.debug("tournaments failed for %s: %s", country, exc)
            return country, continent, None
        return country, continent, (data or {}).get("tournaments")

    results = await _gather(
        (one(rcid, name, continent) for rcid, name, continent in categories), "leagues"
    )

    leagues: dict[int, League] = {}
    for country, continent, tournaments in results:
        _collect(country, continent, sport, tournaments, leagues)

    ordered = sorted(
        leagues.values(),
        key=lambda l: (l.continent, l.country, l.level_order, l.name),
    )
    log.info("%d %s competitions with a live season", len(ordered), sport.lower())
    return ordered


def _as_list(matches: Any) -> list[dict]:
    if isinstance(matches, dict):
        return list(matches.values())
    if isinstance(matches, list):
        return matches
    return []


async def fetch_fixtures(feed: Feed, league: League, window_hours: float) -> list[Match]:
    """Upcoming, not-yet-played fixtures for one league inside the window."""
    now = int(time.time())
    horizon = now + int(window_hours * 3_600)

    try:
        data = Feed.payload(await feed.get(f"stats_season_fixtures/{league.season_id}"))
    except FeedError as exc:
        log.debug("fixtures failed for %s: %s", league.label, exc)
        return []
    if not data:
        return []

    out: list[Match] = []
    for raw in _as_list(data.get("matches")):
        if not isinstance(raw, dict):
            continue
        if raw.get("postponed") or raw.get("canceled") or raw.get("tobeannounced"):
            continue
        if (raw.get("result") or {}).get("winner") is not None:
            continue

        uts = ((raw.get("time") or {}).get("uts")) or 0
        if not (now <= uts <= horizon):
            continue

        teams = raw.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        if not home.get("uid") or not away.get("uid"):
            continue

        out.append(
            Match(
                match_id=int(raw["_id"]),
                league=league,
                home_name=home.get("mediumname") or home.get("name") or "?",
                away_name=away.get("mediumname") or away.get("name") or "?",
                home_uid=int(home["uid"]),
                away_uid=int(away["uid"]),
                kickoff_uts=int(uts),
            )
        )
    return out


async def fetch_h2h(feed: Feed, match: Match) -> H2HRow | None:
    """Head-to-head record for one fixture, or None when it has no stats."""
    # The feed rejects the pair unless the two team uids ascend, regardless of
    # which side is at home ("Unique team parameters are required to be in
    # ascending order"). The response itself is keyed by uid, so home/away is
    # still resolved correctly below.
    low, high = sorted((match.home_uid, match.away_uid))
    path = f"stats_h2h_versus/{low}/{high}/{match.match_id}"
    try:
        data = Feed.payload(await feed.get(path))
    except FeedError as exc:
        log.debug("h2h failed for %s: %s", match.fixture, exc)
        return None
    if not data:
        return None

    # Step 3 of the brief: only matches flagged with the stats icon.
    coverage = (data.get("match") or {}).get("coverage") or {}
    if not coverage.get("hasstats"):
        return None

    stats = (data.get("versusmatchstats") or {}).get(str(match.home_uid))
    if not stats:
        return None

    total = int((stats.get("totalmatches") or {}).get("total") or 0)
    if total <= 0:
        return None  # flagged for stats, but the teams have never met

    home_wins = int((stats.get("teamwins") or {}).get("total") or 0)
    draws = int((stats.get("teamdraws") or {}).get("total") or 0)
    away_wins = int((stats.get("teamloses") or {}).get("total") or 0)

    if home_wins + draws + away_wins != total:
        log.debug("inconsistent H2H totals for %s, skipping", match.fixture)
        return None

    return H2HRow(
        match=match,
        total_meetings=total,
        home_wins=home_wins,
        draws=draws,
        away_wins=away_wins,
        home_pct=_pct(home_wins, total),
        draw_pct=_pct(draws, total),
        away_pct=_pct(away_wins, total),
    )


async def _gather(tasks: Iterable, label: str) -> list:
    """Run tasks concurrently, logging progress and swallowing failures."""
    tasks = list(tasks)
    results: list = []
    done = 0
    for chunk_start in range(0, len(tasks), 50):
        chunk = tasks[chunk_start : chunk_start + 50]
        for item in await asyncio.gather(*chunk, return_exceptions=True):
            done += 1
            if isinstance(item, Exception):
                continue
            results.append(item)
        log.info("%s: %d/%d", label, done, len(tasks))
    return results


async def run(
    token: str,
    sport_id: int = DEFAULT_SPORT,
    window_hours: float = 24.0,
    league_filter: str | None = None,
    limit_leagues: int | None = None,
    concurrency: int = 8,
) -> list[H2HRow]:
    """Full pipeline. Returns one row per upcoming match that has H2H stats."""
    async with Feed(token, concurrency=concurrency) as feed:
        leagues = await load_leagues(feed, sport_id)

        if league_filter:
            needle = league_filter.lower()
            leagues = [l for l in leagues if needle in l.label.lower()]
            log.info("league filter %r -> %d leagues", league_filter, len(leagues))
        if limit_leagues:
            leagues = leagues[:limit_leagues]

        if not leagues:
            log.warning("no leagues matched")
            return []

        fixture_lists = await _gather(
            (fetch_fixtures(feed, l, window_hours) for l in leagues), "fixtures"
        )
        matches = [m for sub in fixture_lists for m in sub]
        log.info(
            "%d upcoming matches in the next %g hours across %d leagues",
            len(matches), window_hours, len(leagues),
        )
        if not matches:
            return []

        rows = await _gather((fetch_h2h(feed, m) for m in matches), "h2h")
        rows = [r for r in rows if r is not None]
        log.info("%d matches have H2H stats available", len(rows))

    rows.sort(key=lambda r: (r.match.league.continent, r.match.league.country,
                             r.match.league.level_order, r.match.league.name,
                             r.match.kickoff_uts))
    return rows
