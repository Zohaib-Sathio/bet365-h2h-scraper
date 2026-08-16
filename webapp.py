#!/usr/bin/env python
"""Live web front-end for the bet365 / StatsHub H2H extractor.

Serves one page listing every upcoming football match bet365 shows a statistics
icon for, together with the head-to-head win / draw / loss percentages, and
lets the visitor download the same table as Excel or CSV.

    python -m uvicorn webapp:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse

from bet365_h2h import scraper
from bet365_h2h.config import DEFAULT_SPORT, SPORTS
from bet365_h2h.excel import to_dataframe, to_records, write_workbook
from bet365_h2h.feed import Feed
from bet365_h2h.models import H2HRow, League
from bet365_h2h.token import get_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("webapp")

app = FastAPI(title="bet365 H2H Percentages", docs_url=None, redoc_url=None)

# A full sweep across every competition takes a couple of minutes, so results
# are cached and shared by all visitors: one refresh serves everyone.
CACHE_TTL = 20 * 60
LEAGUE_TTL = 6 * 3600
DEFAULT_HOURS = 6

# Caches are keyed by (sport id, window) so football and basketball never
# overwrite one another.
_cache: dict[tuple[int, float], tuple[float, list[H2HRow]]] = {}
_locks: dict[tuple[int, float], asyncio.Lock] = {}
_leagues: dict[int, tuple[float, list[League]]] = {}
_league_lock = asyncio.Lock()

# Last failure per task, surfaced by /healthz. Render keeps no log the visitor
# can read, so without this an upstream outage is indistinguishable from a
# genuinely empty fixture list.
_errors: dict[str, str] = {}


def _note(task: str, exc: BaseException | None) -> None:
    if exc is None:
        _errors.pop(task, None)
    else:
        _errors[task] = f"{type(exc).__name__}: {exc}"[:300]


def _lock(key: tuple[int, float]) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


def _sport_id(sport: str | None) -> int:
    """Accept a sport name ("Basketball") or id ("2"); default to football."""
    if not sport:
        return DEFAULT_SPORT
    for sid, name in SPORTS.items():
        if sport.strip().lower() in (name.lower(), str(sid)):
            return sid
    return DEFAULT_SPORT


async def get_rows(
    sport_id: int, hours: float, refresh: bool = False
) -> tuple[list[H2HRow], float]:
    """Cached scrape for one sport and window. Returns (rows, fetched_at)."""
    key = (sport_id, hours)
    async with _lock(key):
        hit = _cache.get(key)
        if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
            return hit[1], hit[0]

        name = SPORTS.get(sport_id, str(sport_id))
        log.info("scraping %s, %g-hour window...", name, hours)
        try:
            token = await asyncio.to_thread(get_token)
            rows = await scraper.run(token.value, sport_id=sport_id, window_hours=hours)
            _note(f"rows:{name}:{hours:g}", None)
        except Exception as exc:
            rows = []
            _note(f"rows:{name}:{hours:g}", exc)
            log.error("scrape failed for %s / %g h: %s", name, hours, exc)

        # Serve the previous sweep rather than an empty page when the feed is
        # briefly unavailable. An empty result is only cached if there was
        # nothing cached before.
        if not rows and hit:
            log.warning("keeping %d cached %s rows", len(hit[1]), name)
            return hit[1], hit[0]

        stamp = time.time()
        _cache[key] = (stamp, rows)
        log.info("cached %d %s rows for the %g-hour window", len(rows), name, hours)
        return rows, stamp


async def get_leagues(sport_id: int, refresh: bool = False) -> list[League]:
    """Every competition bet365 carries, regardless of the fixture window.

    The filter tree is built from this rather than from the matches on screen,
    so a league stays listed even when its next fixture falls outside the
    window the visitor picked.
    """
    async with _league_lock:
        hit = _leagues.get(sport_id)
        if hit and not refresh and time.time() - hit[0] < LEAGUE_TTL:
            return hit[1]

        try:
            token = await asyncio.to_thread(get_token)
            async with Feed(token.value) as feed:
                leagues = await scraper.load_leagues(feed, sport_id)
            _note(f"leagues:{SPORTS.get(sport_id, sport_id)}", None)
        except Exception as exc:
            leagues = []
            _note(f"leagues:{SPORTS.get(sport_id, sport_id)}", exc)
            log.error("league refresh failed for sport %s: %s", sport_id, exc)

        # Never replace a good list with an empty one. A transient upstream
        # hiccup would otherwise leave the filters blank until the next refresh.
        if not leagues and hit:
            log.warning("keeping %d cached leagues for sport %s", len(hit[1]), sport_id)
            return hit[1]

        _leagues[sport_id] = (time.time(), leagues)
        return leagues


def _filter(
    rows: list[H2HRow],
    continent: str | None,
    country: str | None,
    league: str | None,
) -> list[H2HRow]:
    def keep(row: H2HRow) -> bool:
        lg = row.match.league
        if continent and continent != "all" and lg.continent != continent:
            return False
        if country and country != "all" and lg.country != country:
            return False
        if league and league != "all" and lg.name != league:
            return False
        return True

    return [r for r in rows if keep(r)]


@app.on_event("startup")
async def prewarm() -> None:
    """Fill the caches in the background so the first visitor is not left waiting."""

    async def worker() -> None:
        try:
            for sid in SPORTS:
                await get_leagues(sid)
            await get_rows(DEFAULT_SPORT, DEFAULT_HOURS)
        except Exception as exc:  # a cold-start failure must not kill the server
            log.error("pre-warm failed: %s", exc)

    asyncio.create_task(worker())


@app.get("/api/leagues")
async def api_leagues(sport: str | None = None, refresh: bool = False):
    """Continent -> country -> league tree behind the cascading filters."""
    sport_id = _sport_id(sport)
    leagues = await get_leagues(sport_id, refresh)

    tree: dict[str, dict[str, list[str]]] = {}
    for lg in leagues:
        names = tree.setdefault(lg.continent, {}).setdefault(lg.country, [])
        if lg.name not in names:
            names.append(lg.name)

    return {
        "sport": SPORTS.get(sport_id, str(sport_id)),
        "sports": list(SPORTS.values()),
        "total": len(leagues),
        "continents": [
            {
                "name": continent,
                "countries": [
                    {"name": country, "leagues": sorted(names)}
                    for country, names in sorted(countries.items())
                ],
            }
            for continent, countries in sorted(tree.items())
        ],
    }


@app.get("/api/matches")
async def api_matches(
    sport: str | None = None,
    hours: float = Query(DEFAULT_HOURS, gt=0, le=720),
    continent: str | None = None,
    country: str | None = None,
    league: str | None = None,
    refresh: bool = False,
):
    sport_id = _sport_id(sport)
    rows, stamp = await get_rows(sport_id, hours, refresh)
    selected = _filter(rows, continent, country, league)
    return {
        "updated": datetime.fromtimestamp(stamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
        "sport": SPORTS.get(sport_id, str(sport_id)),
        "hours": hours,
        "total": len(rows),
        "shown": len(selected),
        "rows": to_records(selected, detailed=True),
    }


def _stem(
    sport: str | None, continent: str | None, country: str | None, league: str | None
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [p for p in (sport, continent, country, league) if p and p != "all"]
    label = parts[-1] if parts else "all_leagues"
    slug = "".join(c if c.isalnum() else "_" for c in label).strip("_")
    return f"h2h_{slug}_{today}"


@app.get("/export.xlsx")
async def export_xlsx(
    sport: str | None = None,
    hours: float = Query(DEFAULT_HOURS, gt=0, le=720),
    continent: str | None = None,
    country: str | None = None,
    league: str | None = None,
    detailed: bool = True,
):
    rows, _ = await get_rows(_sport_id(sport), hours)
    buffer = io.BytesIO()
    write_workbook(_filter(rows, continent, country, league), buffer, detailed=detailed)
    buffer.seek(0)
    name = _stem(sport, continent, country, league)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


@app.get("/export.csv")
async def export_csv(
    sport: str | None = None,
    hours: float = Query(DEFAULT_HOURS, gt=0, le=720),
    continent: str | None = None,
    country: str | None = None,
    league: str | None = None,
    detailed: bool = True,
):
    rows, _ = await get_rows(_sport_id(sport), hours)
    frame = to_dataframe(_filter(rows, continent, country, league), detailed=detailed)
    payload = frame.to_csv(index=False).encode("utf-8-sig")
    name = _stem(sport, continent, country, league)
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "cached": [
            {
                "sport": SPORTS.get(sid, str(sid)),
                "hours": hrs,
                "rows": len(_cache[(sid, hrs)][1]),
            }
            for sid, hrs in sorted(_cache)
        ],
        "leagues_known": {
            SPORTS.get(sid, str(sid)): len(v[1]) for sid, v in _leagues.items()
        },
        "errors": _errors,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return PAGE


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Football H2H Percentages</title>
<style>
  :root {
    --bg:#0f1512; --panel:#161e1a; --line:#24312b; --ink:#e8f0ec;
    --muted:#8fa79c; --accent:#2f9e6b; --accent2:#1f4e3d;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header { padding:22px 26px; background:linear-gradient(90deg,#143024,#0f1512);
           border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:20px; letter-spacing:.2px; }
  header p { margin:6px 0 0; color:var(--muted); font-size:13px; max-width:80ch; }
  .bar { display:flex; flex-wrap:wrap; gap:12px; align-items:center;
         padding:14px 26px; border-bottom:1px solid var(--line); background:var(--panel); }
  label { color:var(--muted); font-size:13px; margin-right:6px; }
  select, input[type=search] { background:#0e1512; color:var(--ink);
         border:1px solid var(--line); border-radius:8px; padding:8px 10px;
         font-size:14px; max-width:240px; }
  button { background:var(--accent); color:#04120b; border:0; border-radius:8px;
           padding:9px 14px; font-size:14px; font-weight:600; cursor:pointer; }
  button.ghost { background:transparent; color:var(--ink); border:1px solid var(--line); }
  button:disabled { opacity:.5; cursor:default; }
  .spacer { flex:1; }
  .meta { padding:10px 26px; color:var(--muted); font-size:13px; }
  .wrap { padding:0 26px 40px; overflow-x:auto; }
  table { border-collapse:collapse; width:100%; min-width:940px; }
  th, td { padding:10px 12px; border-bottom:1px solid var(--line); text-align:left;
           vertical-align:top; }
  th { position:sticky; top:0; background:var(--accent2); color:#fff; font-size:13px;
       text-transform:uppercase; letter-spacing:.4px; }
  td.num { width:130px; }
  tr:hover td { background:#14201a; }
  .league { color:var(--muted); font-size:12px; }
  .cell { display:flex; align-items:center; gap:8px; }
  .track { flex:1; height:6px; border-radius:3px; background:#22302a; overflow:hidden; }
  .fill { display:block; height:6px; background:var(--accent); }
  b.pct { font-variant-numeric:tabular-nums; width:38px; text-align:right; }
  a.src { color:var(--accent); text-decoration:none; font-size:12px; white-space:nowrap; }
  a.src:hover { text-decoration:underline; }
  .empty { padding:40px 26px; color:var(--muted); text-align:center; }
  .spin { display:inline-block; width:13px; height:13px; border:2px solid var(--muted);
          border-top-color:transparent; border-radius:50%; animation:r .8s linear infinite;
          vertical-align:-2px; margin-right:8px; }
  @keyframes r { to { transform:rotate(360deg); } }
</style>
</head>
<body>
<header>
  <h1>Head-to-head percentages</h1>
  <p>Upcoming football and basketball fixtures that carry bet365's statistics
     icon, with the historical home / draw / away split for every pairing. Each
     row links to bet365's own statistics page for that match, so every number
     can be checked at the source. Kick-off times are UTC.</p>
</header>

<div class="bar">
  <div><label for="sport">Sport</label>
    <select id="sport">
      <option value="Football" selected>Football</option>
      <option value="Basketball">Basketball</option>
    </select>
  </div>
  <div><label for="hours">Window</label>
    <select id="hours">
      <option value="3">Next 3 hours</option>
      <option value="6" selected>Next 6 hours</option>
      <option value="12">Next 12 hours</option>
      <option value="24">Next 24 hours</option>
      <option value="72">Next 3 days</option>
      <option value="168">Next 7 days</option>
      <option value="336">Next 14 days</option>
    </select>
  </div>
  <div><label for="continent">Continent</label>
    <select id="continent"><option value="all">All continents</option></select>
  </div>
  <div><label for="country">Country</label>
    <select id="country"><option value="all">All countries</option></select>
  </div>
  <div><label for="league">League</label>
    <select id="league"><option value="all">All leagues</option></select>
  </div>
  <div><input id="q" type="search" placeholder="Search team..."></div>
  <div class="spacer"></div>
  <button id="xlsx">Download Excel</button>
  <button id="csv" class="ghost">CSV</button>
  <button id="reload" class="ghost">Refresh data</button>
</div>

<div class="meta" id="meta"><span class="spin"></span>Loading fixtures...</div>
<div class="wrap"><table id="tbl">
  <thead><tr>
    <th>Match</th><th>Home Team Win %</th><th id="drawhead">Draw %</th>
    <th>Away Team Win %</th>
    <th>Kick-off (UTC)</th><th>Previous meetings</th><th>Source</th>
  </tr></thead>
  <tbody></tbody>
</table></div>

<script>
var ROWS = [];
var TREE = { continents: [] };
var UPDATED = "";
// Requests are fired on every filter change, and a slow one must never
// overwrite the result of a newer one, so each load carries a sequence number.
var SEQ = 0;
function $(id) { return document.getElementById(id); }

function esc(s) {
  return String(s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}

function params(extra) {
  var p = new URLSearchParams({
    sport: $("sport").value,
    hours: $("hours").value,
    continent: $("continent").value,
    country: $("country").value,
    league: $("league").value
  });
  for (var k in (extra || {})) p.set(k, extra[k]);
  return p.toString();
}

function options(sel, label, names, keep) {
  sel.innerHTML = '<option value="all">' + label + " (" + names.length + ")</option>" +
    names.map(function (n) {
      return '<option value="' + esc(n) + '">' + esc(n) + "</option>";
    }).join("");
  sel.value = names.indexOf(keep) >= 0 ? keep : "all";
}

function continentsOf() {
  return TREE.continents.map(function (c) { return c.name; });
}

function countriesOf(continent) {
  var out = [];
  TREE.continents.forEach(function (c) {
    if (continent !== "all" && c.name !== continent) return;
    c.countries.forEach(function (co) {
      if (out.indexOf(co.name) < 0) out.push(co.name);
    });
  });
  return out.sort();
}

function leaguesOf(continent, country) {
  var out = [];
  TREE.continents.forEach(function (c) {
    if (continent !== "all" && c.name !== continent) return;
    c.countries.forEach(function (co) {
      if (country !== "all" && co.name !== country) return;
      co.leagues.forEach(function (l) { if (out.indexOf(l) < 0) out.push(l); });
    });
  });
  return out.sort();
}

function syncFilters() {
  var cont = $("continent").value;
  var ctry = $("country").value;
  var lg = $("league").value;
  options($("continent"), "All continents", continentsOf(), cont);
  options($("country"), "All countries", countriesOf($("continent").value), ctry);
  options($("league"), "All leagues",
          leaguesOf($("continent").value, $("country").value), lg);
}

function pctCell(v) {
  return '<td class="num"><div class="cell"><span class="track">' +
         '<span class="fill" style="width:' + v + '%"></span></span>' +
         '<b class="pct">' + v + '%</b></div></td>';
}

function render() {
  // Basketball cannot end level, so the draw column is dropped for it.
  var draws = $("sport").value !== "Basketball";
  $("drawhead").style.display = draws ? "" : "none";
  var needle = $("q").value.trim().toLowerCase();
  var rows = ROWS.filter(function (r) {
    return !needle || r["Match"].toLowerCase().indexOf(needle) >= 0;
  });

  var html = rows.map(function (r) {
    return "<tr><td><div>" + esc(r["Match"]) + "</div>" +
      '<div class="league">' + esc(r["Country"]) + " &middot; " + esc(r["League"]) +
      " (" + esc(r["Continent"]) + ")</div></td>" +
      pctCell(r["Home Team Win %"]) +
      (draws ? pctCell(r["Draw %"]) : "") +
      pctCell(r["Away Team Win %"]) +
      "<td>" + esc(r["Date (UTC)"]) + " " + esc(r["Kick-off (UTC)"]) + "</td>" +
      "<td>" + r["Meetings"] + " (" + r["Home Wins"] + "-" + r["Draws"] + "-" +
      r["Away Wins"] + ")</td>" +
      '<td><a class="src" target="_blank" rel="noopener" href="' +
      esc(r["Source (bet365 stats page)"]) + '">verify &#8599;</a></td></tr>';
  }).join("");

  $("tbl").querySelector("tbody").innerHTML = html ||
    '<tr><td colspan="' + (draws ? 7 : 6) + '" class="empty">' +
    "No fixtures for this filter inside the " +
    "selected window &mdash; try a wider window.</td></tr>";
  $("meta").textContent = rows.length + (rows.length === 1 ? " match" : " matches") +
    " shown \\u00b7 data updated " + UPDATED;
}

function loadTree() {
  return fetch("/api/leagues?sport=" + encodeURIComponent($("sport").value))
    .then(function (r) { return r.json(); })
    .then(function (data) { TREE = data; syncFilters(); })
    .catch(function () { /* filters stay on "all" if the tree is unavailable */ });
}

function load(refresh) {
  var seq = ++SEQ;
  $("meta").innerHTML = '<span class="spin"></span>' + (refresh
    ? "Re-scraping live data - this takes a minute or two..."
    : "Loading fixtures...");
  var buttons = document.querySelectorAll("button");
  for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;

  fetch("/api/matches?" + params(refresh ? { refresh: "true" } : {}))
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      if (seq !== SEQ) return;          // a newer request is already in flight
      UPDATED = data.updated;
      ROWS = data.rows;
      render();
    })
    .catch(function (err) {
      if (seq !== SEQ) return;
      $("meta").textContent = "Could not load data: " + err.message;
    })
    .then(function () {
      if (seq !== SEQ) return;
      for (var i = 0; i < buttons.length; i++) buttons[i].disabled = false;
    });
}

$("sport").onchange = function () {
  $("continent").value = "all";
  $("country").value = "all";
  $("league").value = "all";
  loadTree().then(function () { load(false); });
};
$("hours").onchange = function () { load(false); };
$("continent").onchange = function () { syncFilters(); load(false); };
$("country").onchange = function () { syncFilters(); load(false); };
$("league").onchange = function () { load(false); };
$("q").oninput = render;
$("reload").onclick = function () { load(true); };
$("xlsx").onclick = function () { location.href = "/export.xlsx?" + params(); };
$("csv").onclick = function () { location.href = "/export.csv?" + params(); };

loadTree();
load(false);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # Hosting platforms hand the port over in $PORT (Render, Railway, Fly);
    # Hugging Face Spaces expects 7860.
    import os

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
