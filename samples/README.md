# Verified feed samples

Captured live 2026-08-14 and used as ground truth while writing the parsers.
See HANDOFF.md section 2 for the field map.

- `stats_h2h_versus_7_48_72221158.json` — Everton vs Crystal Palace. Contains
  `match.coverage.hasstats` and `versusmatchstats`. Home uid 48: wins 17,
  draws 13, losses 6, total 36 -> 47% / 36% / 17%, matching the PDF exactly.
- `stats_season_fixtures_140756.SAMPLE.json` — Premier League 26/27 fixtures,
  trimmed to the first 5 matches. Full response is 366 KB / 380 matches.
- `config_tournaments_1_17.json` — tournaments for one category, with seasonids.
- `stats_team_versus_48_7_.json` — the wider team-vs-team feed (not used by the
  pipeline; kept for reference).
