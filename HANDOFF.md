# HANDOFF — bet365 H2H scraper

Status as of **2026-08-14**. Project ID 40645984 (Freelancer), client Abenezer
(@beni57234), agreed bid $30. Deliverable: working tool + sample Excel + a live
page / demo the client can see before awarding.

**The tool is finished and running live.** Everything below is context for
maintaining it.

---

## 1. Data source

The H2H percentages are not on bet365 itself. bet365 embeds Sportradar
StatsHub in an iframe:

```
statshub.sportradar.com/bet365de/en/match/72221158
```

which is backed by the JSON feed

```
https://sh.fn.sportradar.com/bet365de/en/Etc:UTC/gismo/<endpoint>?T=<token>
```

Required headers: `Origin: https://statshub.sportradar.com`,
`Referer: https://statshub.sportradar.com/`, a normal desktop `User-Agent`.
bet365's own anti-bot stack is never touched, so the client's worry about
betting sites blocking scrapers does not apply.

Response envelope: `{ "queryUrl": ..., "doc": [ { "data": {...} } ] }`,
unwrapped by `Feed.payload()`.

### Endpoints in use

| Purpose | Endpoint |
|---|---|
| Countries / regions | `config_tree/1` |
| Competitions per country + current season | `config_tournaments/1/{rcid}` |
| Fixtures for a season | `stats_season_fixtures/{seasonId}` |
| Stats flag + percentages | `stats_h2h_versus/{uidLow}/{uidHigh}/{matchId}` |

Dead ends (empty ~150-250 B stubs): `config_categories/1`, `config_sports`,
`stats_category_list/1`, `config_default_category_tournaments/1`,
`config_tournaments_list/1`, `stats_season_matches/{id}`,
`season_fixtures/{id}`. `feed.mapi.sportradar.com` returns 401 for everything;
the `mapiAppKey` in the page config authorises nothing — the `T=` token does.

### Field map

`stats_season_fixtures/{seasonId}` -> `data.matches` (dict or list):
`_id`, `time.uts`, `result.winner` (`null` = not played), `teams.home.uid` /
`teams.away.uid` (**`uid`, not `_id`**), plus `postponed` / `canceled` /
`tobeannounced` to filter out.

`stats_h2h_versus/...` -> `data`:
`match.coverage.hasstats` is the statistics icon from step 3 of the brief;
`versusmatchstats["<uid>"]` holds `totalmatches.total`, `teamwins.total`
(home wins), `teamdraws.total`, `teamloses.total` (away wins).

Ground truth, matching the client's PDF screenshot exactly — Everton vs
Crystal Palace, match `72221158`, home uid 48, away uid 7: 36 meetings,
17 (47%) / 13 (36%) / 6 (17%). Rounding is half-up, `floor(x*100 + 0.5)`,
not Python's banker's rounding — see `_pct()` in `scraper.py`.

---

## 2. Three things that were solved

1. **The token.** Headless Chromium gets a 403 from Akamai on the StatsHub
   page, which used to block the whole pipeline. It turned out the signed
   token is embedded in the server-rendered HTML under the key
   `fishnetToken`, which plain `httpx` fetches fine. `token.py` now scrapes it
   with one GET and caches it in `.cache/token.json` (~24 h lifetime, refreshed
   when under 30 min remain). **Playwright is no longer a dependency.**

2. **Token placement.** The token must be passed as `?T=exp=...`. Without the
   `T=` prefix the feed answers HTTP 200 with a 149-byte stub.

3. **Team uid order.** `stats_h2h_versus` rejects the pair unless the two uids
   ascend (`"Unique team parameters are required to be in ascending order."`),
   regardless of who is at home. `fetch_h2h()` sorts them; the response is
   keyed by uid so home/away is still resolved correctly. Reading the away
   team's block instead of the home team's would silently swap the
   percentages.

Also worth knowing: `config_tree` only lists competitions that are mid-season,
so the Premier League was missing in mid-August (it starts on the 22nd).
Leagues are therefore enumerated per country via `config_tournaments`, which
lifted coverage from 252 to ~398 live seasons.

---

## 3. What is built

```
bet365_h2h/
  config.py    feed host, headers, paths, concurrency knobs
  token.py     scrape + cache the signed feed token (pure HTTP)
  feed.py      async httpx client: signing, throttling, retry, envelope unwrap
  models.py    League / Match / H2HRow dataclasses, source-page URL
  scraper.py   sport -> continents/countries -> leagues -> fixtures -> H2H
  excel.py     styled .xlsx + .csv, detailed or four-column layout
webapp.py      FastAPI live page + /api/leagues, /api/matches, /export.*
run.py         CLI
samples/       captured feed responses from the API mapping work
```

Sheet columns (client asked for match + three percentages; detailed layout
keeps the supporting numbers after them):

```
Match | Home Team Win % | Draw % | Away Team Win % |
Sport | Continent | Country | League | Date (UTC) | Kick-off (UTC) |
Home Team | Away Team | Meetings | Home Wins | Draws | Away Wins |
Source (bet365 stats page)
```

`--simple` / `detailed=false` trims to the first four columns.

---

## 4. Live deployment

**https://bet365-h2h.onrender.com** — Render free plan, service
`srv-d9vhsns9v7es73907q3g` (`bet365-h2h`, Frankfurt), built from
`github.com/Zohaib-Sathio/bet365-h2h-scraper` on every push to `main`.
Build `pip install -r requirements.txt`, start
`python -m uvicorn webapp:app --host 0.0.0.0 --port $PORT --proxy-headers`,
health check `/healthz`, `PYTHON_VERSION=3.12.6`.

The free instance sleeps after 15 minutes idle (30-60 s cold start), so a
GitHub Actions cron in `.github/workflows/keepalive.yml` pings `/healthz`
every 10 minutes. 512 MB / 0.1 CPU is enough because the work is network-bound;
the first sweep after boot completed in about a minute.

For a local run instead: `python -m uvicorn webapp:app --port 8000`, optionally
exposed with `ngrok http 8000` (free ngrok shows a one-click interstitial).

Timings measured on the live feed: 6-hour window ≈ 45 s, ~113 matches;
7-day ≈ 90 s, 889 matches; 10-day ≈ 3 min, 1690 matches across ~410 leagues.
Results are cached for 20 minutes and shared by all visitors, and the default
window is pre-warmed at startup.

## 4b. Client feedback of 14 Aug, and how it was answered

**"There are missing leagues — only 5 under England. Add continent / country /
league filters like the website."**

Nothing was missing from the scrape; the sheet was a 7-day window and the
Premier League did not start until 22 Aug. The filter tree is now built from
the full competition list (`/api/leagues`), not from the fixtures on screen, so
England lists all 9 competitions — Premier League, Championship, League One,
League Two, National League, FA Cup, EFL Cup, EFL Trophy, Community Shield —
whatever window is selected. Football resolves to ~410 competitions across
77 countries and 7 continents; the continent comes from `category.cc.continent`
in `config_tree`. Basketball is wired up on the same path (`SPORTS` in
`config.py`); note the feed's per-country tournament endpoint stays
`config_tournaments/1/{rcid}` for every sport — the `1` is not the sport id.

**"The system is generating fictional data when a stat is missing."**

It is not. Every number is read straight from the feed and nothing is inferred
or filled in. The row the client circled, Atlanta United vs New York Red Bulls:
the feed returns `totalmatches.total 21`, `teamwins 4`, `teamdraws 6`,
`teamloses 11`, and bet365's own statistics page for match 66299338 renders
"Matches Played - 21 | Since 2017 — 4 (19%) / 6 (29%) / 11 (52%)". The export
says 19 / 29 / 52 over 21 meetings. To make this checkable without any
explanation, every row now carries a **Source** link to
`statshub.sportradar.com/bet365de/en/match/{matchId}` — the exact page bet365
embeds.

## 5. Remaining / optional

1. Permanent hosting instead of a laptop + ngrok tunnel (Render, Fly, a VPS).
   The free ngrok URL also changes on every restart.
2. Weekly scheduler if the client wants a file on a cadence — Windows Task
   Scheduler calling `run.py --days 7`.
3. Demo video for the client.
4. Basketball rows always show Draw 0% (the sport has no draws) — the column
   could be hidden when a non-football sport is selected.

## 6. Risks

* Undocumented internal API; shape can change without notice. Keep the
  `hasstats` and `versusmatchstats` guards and the divide-by-zero guard for
  pairings that have never met.
* The competition tree under `/bet365de/` is scoped to what bet365 offers,
  which is exactly the client's requirement.
