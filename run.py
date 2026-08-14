#!/usr/bin/env python
"""CLI entry point.

  python run.py                      # all leagues, next 24 hours
  python run.py --hours 6            # tighter window
  python run.py --days 7 --simple    # week ahead, four-column sheet
  python run.py --league "Premier"   # single competition
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from bet365_h2h import scraper
from bet365_h2h.config import DEFAULT_SPORT, MAX_CONCURRENCY, OUTPUT_DIR, SPORTS
from bet365_h2h.excel import export
from bet365_h2h.token import get_token


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bet365-h2h",
        description="Extract H2H percentages for upcoming football matches.",
    )
    parser.add_argument("--sport", default="Football",
                        choices=list(SPORTS.values()),
                        help="sport to scrape (default: Football)")
    parser.add_argument("--hours", type=float, default=24.0,
                        help="look-ahead window in hours (default: 24)")
    parser.add_argument("--days", type=float, default=None,
                        help="look-ahead window in days (overrides --hours)")
    parser.add_argument("--league", default=None,
                        help="only leagues whose name contains this text")
    parser.add_argument("--limit-leagues", type=int, default=None,
                        help="cap the number of leagues (useful for demos)")
    parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY,
                        help=f"parallel feed requests (default: {MAX_CONCURRENCY})")
    parser.add_argument("--out", default=None, help="output file stem")
    parser.add_argument("--simple", action="store_true",
                        help="export only Match / Home %% / Draw %% / Away %%")
    parser.add_argument("--refresh-token", action="store_true",
                        help="ignore the cached token and mint a new one")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs one INFO line per request; at this volume that buries our own
    # progress output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    log = logging.getLogger("run")

    started = datetime.now(timezone.utc)
    hours = args.days * 24 if args.days else args.hours
    sport_id = next((sid for sid, name in SPORTS.items() if name == args.sport),
                    DEFAULT_SPORT)
    log.info("bet365 H2H extractor — %s, next %g hour(s)", args.sport, hours)

    token = get_token(force_refresh=args.refresh_token)

    rows = asyncio.run(
        scraper.run(
            token.value,
            sport_id=sport_id,
            window_hours=hours,
            league_filter=args.league,
            limit_leagues=args.limit_leagues,
            concurrency=args.concurrency,
        )
    )

    if not rows:
        log.warning("no matches with H2H stats found in this window")
        return 1

    stem = args.out or (
        f"h2h_{args.sport.lower()}_{started.strftime('%Y-%m-%d')}"
    )
    xlsx, csv = export(rows, OUTPUT_DIR, stem, detailed=not args.simple)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    leagues = {r.match.league.label for r in rows}
    log.info("done in %.1fs — %d matches across %d leagues", elapsed, len(rows), len(leagues))
    log.info("Excel: %s", xlsx)
    log.info("CSV:   %s", csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
