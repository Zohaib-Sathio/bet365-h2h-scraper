# bet365 H2H percentage extractor

**Live:** <https://bet365-h2h.onrender.com>

Pulls the head-to-head win / draw / loss percentages that bet365 shows behind
the statistics icon for every upcoming football fixture, and serves them as a
live web page plus Excel / CSV downloads.

```
Match                                  Home Team Win %   Draw %   Away Team Win %
Everton FC vs Crystal Palace                        47       36                17
```

## Where the data comes from

bet365 renders its statistics panel from Sportradar StatsHub
(`statshub.sportradar.com/bet365de/...`), which is backed by the JSON feed at
`sh.fn.sportradar.com/bet365de/en/Etc:UTC/gismo/...`. The tool talks to that
feed directly over plain HTTP, so bet365's own anti-bot stack is never touched
and no browser is needed.

Endpoints used:

| Purpose | Endpoint |
|---|---|
| Country / region list, with continent | `config_tree/1` |
| Competitions per country (with current season) | `config_tournaments/1/{rcid}` |
| Fixtures for a season | `stats_season_fixtures/{seasonId}` |
| Statistics flag + the percentages | `stats_h2h_versus/{uidLow}/{uidHigh}/{matchId}` |

Feed calls carry a signed token (`T=exp=...~acl=/*~data=...~hmac=...`) that is
embedded in the StatsHub page HTML under `fishnetToken`. `token.py` scrapes it
with one HTTP GET and caches it in `.cache/token.json` for its ~24 h lifetime.

## Run the live page

```bash
pip install -r requirements.txt
python -m uvicorn webapp:app --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000>. The page offers:

* a sport selector (football, basketball),
* a look-ahead window (3 h to 10 days),
* cascading **continent -> country -> league** filters, built from the full
  competition tree rather than from the fixtures on screen, so a league stays
  listed even when its next match falls outside the chosen window,
* free-text team search,
* a **verify** link on every row that opens bet365's own statistics page for
  that fixture,
* **Download Excel** / **CSV** of exactly what is on screen,
* **Refresh data** to re-scrape on demand.

Results are cached for 20 minutes and shared by all visitors; the cache is
pre-warmed at startup.

To publish it over the internet from a local machine:

```bash
ngrok http 8000
```

### Hosting it properly

`Dockerfile` and `render.yaml` are included; the app reads `$PORT`, so
`python webapp.py` is a valid start command on any platform that injects one.

* **Render (free)** — this is where it runs today, service `bet365-h2h` in
  Frankfurt, deployed from `main` on every push. The free instance sleeps after
  15 minutes idle and cold-starts in 30-60 s, so `.github/workflows/keepalive.yml`
  pings `/healthz` every 10 minutes to keep it warm.
* **A small VPS / Oracle Cloud Always Free** — `docker build . && docker run -p
  80:8000`, always on, no cold starts.
* **Not serverless** (Vercel, Lambda). A full sweep takes minutes and the
  results live in process memory; a per-request function would re-scrape every
  time and hit the platform's duration cap.

## Command line

```bash
python run.py                       # every league, next 24 hours
python run.py --hours 6             # tighter window
python run.py --days 7 --simple     # week ahead, four-column sheet
python run.py --league "Premier League" --out demo_premier
python run.py --refresh-token -v
```

Files land in `output/` as `.xlsx` and `.csv`.

**Sheet layouts.** The default (detailed) layout is:

```
Match | Home Team Win % | Draw % | Away Team Win % |
Sport | Continent | Country | League | Date (UTC) | Kick-off (UTC) |
Home Team | Away Team | Meetings | Home Wins | Draws | Away Wins |
Source (bet365 stats page)
```

`--simple` (CLI) or `detailed=false` (web) trims it to the first four columns.

## Layout

```
bet365_h2h/
  config.py    feed host, headers, paths, concurrency knobs
  token.py     scrape + cache the signed feed token
  feed.py      async httpx client: signing, throttling, retry, envelope unwrap
  models.py    League / Match / H2HRow dataclasses
  scraper.py   leagues -> fixtures -> H2H percentages
  excel.py     styled .xlsx + .csv export
webapp.py      FastAPI live page and download endpoints
run.py         CLI
samples/       captured feed responses used while mapping the API
```

## Notes

* Percentages are rounded half-up, matching the widget (`floor(x*100 + 0.5)`).
  Verified against bet365's own panel: Everton vs Crystal Palace 17 (47%) /
  13 (36%) / 6 (17%) from 36 meetings.
* `stats_h2h_versus` requires the two team uids in **ascending** order; the
  response is keyed by uid, so home and away are still resolved correctly.
* Fixtures without the statistics flag (`coverage.hasstats`) and pairings that
  have never met are skipped, as the brief requires.
* `config_tree` only lists competitions that are mid-season, so leagues are
  enumerated per country through `config_tournaments` — that is what makes
  competitions starting next week (e.g. the Premier League in mid-August)
  appear. Football currently resolves to ~410 competitions across 77
  countries and 7 continents.
* Nothing is ever computed, estimated or filled in. A fixture appears only
  when the feed reports `coverage.hasstats` **and** at least one previous
  meeting; the three percentages are `teamwins`/`teamdraws`/`teamloses` over
  `totalmatches`. The `Source` column links to the page those numbers are
  rendered on, e.g. Atlanta United vs New York Red Bulls shows
  "Matches Played - 21", 4 (19%) / 6 (29%) / 11 (52%) — identical to the
  exported row.
