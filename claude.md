# email-digest — Project Documentation
*Last updated: March 2026*

---

When I open a terminal: source /Users/zpressley/email_digest/venv/bin/activate

## What This Is

A standalone Python service that delivers a personalized fantasy baseball
morning briefing via email every day. Built for Zach Pressley, commissioner
of Fantasy Baseball Pantheon (FBP), a 12-team dynasty H2H categories league
in its 14th season.

The digest replaces manual morning research with a fully automated email
covering roster analysis, pitcher streaming opportunities, free agent heat,
Statcast leading indicators, and prospect callouts — all timed to account
for the Yahoo roster pickup lag.

Runs entirely on GitHub Actions. Zero hosting cost.

---

## The Most Important Design Principle

**Roster lag awareness.** In Yahoo Fantasy, a pickup made today does not
appear on your roster until tomorrow. Every forward-looking alert in this
system accounts for that delay. Streaming opportunities only surface starts
that are at least 2 days out. Free agent alerts always include a "latest add
date" so you know the last possible day to act.

---

## Repo Structure
```
email-digest/
├── .github/
│   └── workflows/
│       ├── daily_digest.yml        # Runs 6 AM CST daily
│       └── weekly_review.yml       # Runs 6 AM CST Sunday
├── src/
│   ├── config.py                   # All env vars + league constants
│   ├── daily_digest.py             # Daily entrypoint
│   ├── weekly_review.py            # Weekly entrypoint
│   ├── data/
│   │   ├── yahoo_client.py         # Yahoo Fantasy API (OAuth2)
│   │   ├── mlb_client.py           # MLB Stats API (free, public)
│   │   ├── statcast_client.py      # Baseball Savant via pybaseball
│   │   └── snapshot_store.py       # Daily JSON snapshot persistence
│   ├── analysis/
│   │   ├── roster_analyzer.py      # Today's roster vs schedule
│   │   ├── pitcher_analyzer.py     # Upcoming starts + league usage
│   │   ├── matchup_finder.py       # Streaming opportunity scorer
│   │   ├── free_agent_tracker.py   # FA heat index with pickup deadlines
│   │   ├── hitter_analyzer.py      # Statcast breakout/bench signals
│   │   ├── category_standings.py   # 20-cat dashboard (weekly)
│   │   └── prospect_tracker.py     # Minor league callouts
│   └── mailer/
│       ├── daily_template.html     # Jinja2 HTML email template (daily)
│       ├── weekly_template.html    # Jinja2 HTML email template (weekly)
│       ├── renderer.py             # Jinja2 render functions
│       └── sender.py               # SendGrid delivery
├── data/
│   ├── snapshots/                  # Daily JSON snapshots (git-tracked)
│   └── baselines/                  # Written by trade-bot pipeline
├── requirements.txt
├── .env.example
└── CLAUDE.md                       # This file
```

---

## How It Works — Daily Flow

At 6 AM CST, GitHub Actions runs `src/daily_digest.py`. Here is what
happens in order:

1. `YahooClient` authenticates using the shared `token.json` from the
   trade bot. If the token is expired it refreshes automatically and writes
   the updated token back so both repos stay in sync.

2. Each analysis module is called in sequence. Each one pulls the data
   it needs from `YahooClient` and/or `MLBClient` independently.

3. All results are assembled into a single `context` dict.

4. The context is saved as a daily JSON snapshot to `data/snapshots/`.

5. Jinja2 renders `daily_template.html` with the context.

6. SendGrid delivers the email to `TO_EMAIL`.

7. GitHub Actions commits the updated snapshot back to the repo.

The weekly flow on Sunday is identical but runs `weekly_review.py` and
renders `weekly_template.html` instead.

---

## Data Sources

| Source | What It Provides | Auth |
|---|---|---|
| Yahoo Fantasy API | My roster, standings, free agents, ownership trends, all team rosters | OAuth2 via token.json |
| MLB Stats API | Schedule, probable starters, player stats, minor league stats, team offense | None — free public API |
| Baseball Savant (pybaseball) | Statcast: barrel rate, exit velocity, xBA, whiff rate, walk rate | None — scrapes public data |
| combined_players.json | Single source of truth for all players, prospects, contracts, IDs | Local file from trade bot |
| Snapshot store | Week-over-week deltas, category trend tracking | Local JSON files |

---

## combined_players.json — Single Source of Truth

This file lives in the trade bot repo at:
`/Users/zpressley/fbp-trade-bot/data/combined_players.json`

It is the authoritative source for all player and prospect data across
every FBP tool — email-digest, trade-bot, and fbp-hub all read from it.
It is written daily by the trade bot pipeline and merges Yahoo roster data
with the Google Sheet player database.

Key fields used by this repo:

| Field | Description |
|---|---|
| `name` | Full player name |
| `player_type` | `"MLB"` or `"Farm"` |
| `manager` | Team abbreviation — e.g. `"WIZ"`, `"HAM"` |
| `upid` | Unique Player ID — primary key across all FBP systems |
| `mlb_id` | MLB Stats API player ID — used for all stat lookups |
| `yahoo_id` | Yahoo Fantasy player ID |
| `position` | Primary position abbreviation |
| `contract_type` | Prospect contract tier (see below) |

**Never use `years_simple` for contract type.** That field tracks keeper
eligibility years, not contract tier. They are completely different things.

---

## Prospect Contract Types

Prospect contracts live in `combined_players.json` under the field
`contract_type`. The three possible values are:

| Raw value in JSON | Display | Cost | Description |
|---|---|---|---|
| `"Purchased Contract"` | PC | $10 WB | Can be called up to Yahoo roster freely |
| `"Development Cont."` | DC | $5 WB | Cheapest tier — MLB debut triggers $15 purchase fee |
| `"Blue Chip Contract"` | BC | $20 WB | Premium tier — Top 100 retention perk on Nov 1 |

The helper function `_format_contract()` in `prospect_tracker.py` handles
the mapping from raw JSON value to display label. Always use this function
when displaying contract types — do not hardcode the raw strings.

---

## Environment Variables

Copy `.env.example` to `.env` for local development. For GitHub Actions,
add these as repository secrets.
```
YAHOO_TOKEN_PATH          Path to token.json (default: ./token.json)
YAHOO_CLIENT_ID           Yahoo app client ID
YAHOO_CLIENT_SECRET       Yahoo app client secret
YAHOO_LEAGUE_ID           FBP league ID (2026 = 8560 — verify each season)
YAHOO_GAME_KEY            MLB season game key (2026 = 469)
YAHOO_TEAM_ID             Your team number in the league (WIZ = 12)
SENDGRID_API_KEY          SendGrid API key for email delivery
TO_EMAIL                  Recipient email address
FROM_EMAIL                Sender address (default: digest@fantasy.local)
COMBINED_PLAYERS_PATH     Path to combined_players.json from trade bot
```

**Important:** `YAHOO_TOKEN_PATH` should point to the same `token.json`
used by the trade bot. In GitHub Actions, the workflow writes the token
from the `YAHOO_TOKEN_JSON` secret at runtime.

**Season updates required each March:**
- `YAHOO_LEAGUE_ID` — verify the league ID for the new season
- `YAHOO_GAME_KEY` — MLB game key changes every year (2026 = 469)

---

## The Yahoo Client

`src/data/yahoo_client.py` handles all Yahoo Fantasy API communication.

Key design decisions:

- Shares `token.json` with the trade bot. Refreshes write back to the
  same file so both repos always have a valid token.
- Uses game key `469` for 2026. Do NOT use the generic `"mlb"` string.
- All Yahoo API responses are XML, parsed with `ElementTree`.
- `YAHOO_TEAM_MAP` maps Yahoo numeric team IDs to FBP abbreviations.
  Must match the trade bot mapping exactly.

Methods:

`get_my_roster()` — All players on my active roster as normalized dicts
including name, position, eligible positions, MLB team, status, injury note.

`get_league_standings()` — All 12 teams with current category stat totals
keyed by Yahoo stat ID.

`get_free_agents(position, limit)` — Available free agents filtered by
position, with ownership percentage.

`get_ownership_trends()` — Players sorted by 48-hour adds. Powers the
free agent heat index.

`get_all_team_rosters()` — Every team's full roster. Used by the weekly
pitcher usage report.

---

## The MLB Client

`src/data/mlb_client.py` wraps `https://statsapi.mlb.com/api/v1`.
No authentication required.

`get_schedule(target_date)` — All games for a date with probable pitchers
and lineups hydrated.

`get_probable_starters(days_ahead)` — Confirmed probable starters across
the next N days with `days_out` field.

`get_player_recent_stats(player_id, days)` — Rolling stats over last N days
using the `byDateRange` endpoint.

`get_minor_league_stats(player_id)` — Minor league stats using sport IDs
11 (AAA), 12 (AA), 13 (A+), 14 (A).

`get_team_offense_rankings(days)` — Teams ranked by runs scored over a
rolling window. Falls back to season totals if date range returns empty.

---

## The Statcast Client

`src/data/statcast_client.py` uses `pybaseball` to pull from Baseball Savant.

Leading indicators tracked:

- **Barrel rate** — rising barrel rate predicts power before box scores show it
- **Average exit velocity** — quality of contact trending up
- **xBA vs actual BA gap** — positive gap means due for upward regression
- **Whiff rate** — falling = improving contact
- **Walk rate** — rising = better plate discipline

Signal scores above 3.0 surface as breakout candidates. Below -2.0 surface
as bench candidates. Minimum 15 PA required to filter small sample noise.

Note: pybaseball queries run 3–8 seconds per player. A 15-hitter roster
adds ~60–90 seconds to run time. Acceptable for a 6 AM cron job.

---

## Analysis Modules

### `roster_analyzer.py`

Crosses my Yahoo roster against today's MLB schedule. Grades each hitter's
matchup as favorable (opponent SP ERA ≥ 4.50), neutral, or tough
(ERA ≤ 3.25). Handles Yahoo vs MLB team abbreviation mismatches via
`_fuzzy_team_match()`.

### `pitcher_analyzer.py`

`get_my_upcoming_starts()` — Matches my rostered pitchers against
`mlb_client.get_probable_starters()` using last-name fuzzy matching.
Flags any start within `ROSTER_LAG_DAYS` as `act_now: True`.

`get_league_pitcher_usage()` — Counts SPs vs RPs per team across all 12
rosters. Flags extremes: heavy load (6+ starters), bullpen heavy
(6+ relievers), low usage (3 or fewer starters). Used in weekly review.

### `matchup_finder.py`

Finds unowned/low-owned SPs (under 30% owned) facing weak offenses within
the 5-day streaming window. Excludes starts within `ROSTER_LAG_DAYS`.

Composite score: `pitcher_score` (rewards low ERA) + `opponent_score`
(rewards worse offenses) + `timing_score` (rewards starts further out).
Skips pitchers with ERA above 5.50. Returns top 6 sorted by score.

`_get_team_offense_rankings()` fetches rolling 14-day team run totals.
Falls back to `_season_offense_rankings()` early in the season.

### `free_agent_tracker.py`

Calls `yahoo_client.get_ownership_trends()` for players sorted by 48-hour
adds. Filters to under 30% owned with positive trend. Calculates
`latest_add_date` as confirmed start date minus `ROSTER_LAG_DAYS`. Returns
top 10 rising players.

### `hitter_analyzer.py`

Wraps the Statcast client. `get_breakout_watch()` returns top 5 hitters
with signal score ≥ 3.0 and ≥ 15 PA. `get_bench_candidates()` returns
top 3 most concerning hitters using negative signal scoring.

### `prospect_tracker.py`

**Data source: `combined_players.json` — not Yahoo API.**

Loads all players where `player_type == "Farm"` and `manager == "WIZ"`.
Uses `mlb_id` from the file directly — no ID guesswork. Fetches minor
league stats from MLB Stats API. Evaluates batters against OPS/AVG
thresholds and pitchers against ERA thresholds. Flags call-up watch
candidates (OPS ≥ 1.000 or ERA ≤ 1.50).

Contract type comes from `contract_type` field — values are
`"Purchased Contract"` (PC), `"Development Cont."` (DC),
`"Blue Chip Contract"` (BC). Normalized via `_format_contract()`.

### `category_standings.py` (weekly — stub)

Pulls league standings from Yahoo and maps stat IDs to the 20 FBP
categories. Needs Yahoo stat ID mapping implemented. Hit the league
settings endpoint to get the current mapping:
`GET /fantasy/v2/league/{league_key}/settings`

---

## Snapshot Store

`src/data/snapshot_store.py` saves one JSON file per day to
`data/snapshots/YYYY-MM-DD.json`. Committed back to the repo by GitHub
Actions after each run. `load_latest_snapshot()` walks back up to 7 days
to find the most recent snapshot, handling gaps gracefully.

---

## Email Templates

Both templates live in `src/mailer/` and are rendered with Jinja2.
Inline CSS only — no external stylesheets — for Gmail compatibility.

**Note:** The folder is named `mailer/` not `email/` to avoid shadowing
Python's built-in `email` standard library module. Do not rename it back.

Daily template sections in order: Today's Roster Impact, Upcoming Pitcher
Starts, Streaming Opportunities, Free Agent Heat Index, Statcast Breakout
Watch, Consider Benching (conditional), Prospect Callouts (conditional).

Conditional sections only render if they contain data.

---

## GitHub Actions

### `daily_digest.yml`
Cron: `0 11 * * *` (6 AM CST = 11 AM UTC)

Steps: checkout → install deps → write `token.json` from secret →
run `daily_digest.py` → commit updated snapshots → push.

### `weekly_review.yml`
Cron: `0 11 * * 0` (6 AM CST Sunday)

Same pattern, runs `weekly_review.py`.

Branch note: If you add auto-deploy triggers, ensure snapshot commits
include `[skip ci]` in the message or use path filters to prevent
redeploys on every snapshot commit.

---

## Relationship to Other Repos

**`fbp-trade-bot`** — Discord bot + FastAPI backend. Runs the daily data
pipeline that writes `combined_players.json`. Contains `token_manager.py`
(OAuth2) and `calculate_baselines.py`. The `token.json` this repo uses is
shared with the trade bot — whichever runs last writes the refreshed token.

**`fbp-hub`** — GitHub Pages frontend at PantheonLeague.com. Hosts
`league_baselines.json` for the Chrome extension. This repo does not
depend on `fbp-hub`.

**`email-digest` (this repo)** — Standalone. Reads from Yahoo API, MLB
API, and `combined_players.json` from the trade bot. Writes daily
snapshots back to itself only.

---

## League Context

FBP is a 12-team dynasty H2H categories league in its 14th season (2026).

**Hitting categories:** R, HR, RBI, SB, AVG, OBP, SLG, OPS, TB, NSB

**Pitching categories:** W, SV, K, ERA, WHIP, K9, BB9, QS, HLD, SVHD

Each team has a 26-man active Yahoo roster plus 4 FBP Hub spots for
called-up prospects (the 30-man rule). 5 NA slots for minor leaguers.

Yahoo league ID: `8560` (2026) — verify each season.
Yahoo game key: `469` (2026) — changes every season, update each March.
WIZ team ID: `12`

Team abbreviations: WIZ (Zach), HAM, B2J, CFL, JEP, LFB, LAW, SAD,
DRO, RV, TBB, WAR.

---

## Prospect Contract Types (Critical — Read This)

Prospect contracts in `combined_players.json` use the field `contract_type`.

| JSON value | Display | Cost | Notes |
|---|---|---|---|
| `"Purchased Contract"` | PC | $10 WB | Free call-up/send-down to Yahoo roster |
| `"Development Cont."` | DC | $5 WB | MLB debut triggers $15 purchase fee |
| `"Blue Chip Contract"` | BC | $20 WB | Top 100 retention perk on Nov 1 |

**Do NOT use `years_simple` for contract type.** That field is keeper
eligibility year tracking. Completely different concept.

Always use `_format_contract()` from `prospect_tracker.py` to normalize
raw values to display labels.

---

## Running Locally
```bash
cd /Users/zpressley/email_digest
source venv/bin/activate
PYTHONPATH=. python3 src/daily_digest.py
PYTHONPATH=. python3 src/weekly_review.py
```

**Preview without sending email** — swap the last lines of `daily_digest.py`:
```python
with open("digest_preview.html", "w") as f:
    f.write(html)
print("Saved to digest_preview.html")
# send_email(...)
```

Then: `open digest_preview.html`

---

## What Is Not Built Yet

**AI Farm System Report** — Claude API (Haiku) writes a 2–3 sentence
narrative per prospect based on minor league stats. Groups into
hot/cold/watch. Needs `ANTHROPIC_API_KEY` in `.env` and a new
`src/data/ai_client.py`. Cost: ~$0.002/day.

**Baseball Pulse** — IFTTT pipes curated Twitter/X accounts into a
`#twitter-feed` Discord channel. Digest bot reads last 24 hours via
Discord API, passes to Claude API, returns narrative summary. The Discord
bot already has the API patterns needed.

**Weekly category standings** — `category_standings.py` is stubbed.
Needs Yahoo stat ID → FBP category mapping implemented.

**Full weekly review** — `weekly_review.py` entrypoint is wired but
category dashboard and pitcher usage sections need stubs filled.

---

## Known Gotchas

**Yahoo vs MLB team abbreviations** — `roster_analyzer._fuzzy_team_match()`
handles known mismatches: CWS/CHW, TB/TBR, SF/SFG, SDP/SD, KCR/KC,
WSN/WSH, ARI/AZ. Add new mismatches here if a player's games aren't showing.

**pybaseball rate limiting** — Baseball Savant throttles occasionally.
Client returns empty dict rather than crashing. If consistently empty,
add `time.sleep(1)` between player lookups in `get_breakout_signals()`.

**Early season data gaps** — First 14 days of season, rolling offense
rankings have small samples. Matchup finder falls back to season totals
automatically. Treat early-season streaming scores with skepticism.

**Yahoo stat IDs** — Numeric IDs in standings responses don't match
category names. When implementing `category_standings.py`, hit the
settings endpoint to get the current mapping:
`GET /fantasy/v2/league/{league_key}/settings`

**Token expiry** — Yahoo tokens expire hourly. `YahooClient.authenticate()`
checks expiry with a 60-second buffer and refreshes proactively. On 401
errors the client also retries once with a forced refresh.

**mailer vs email folder** — The email template folder is named `mailer/`
not `email/`. This is intentional — Python 3.13 shadows the built-in
`email` module if a local folder has the same name, breaking `urllib3`.
Do not rename it back to `email/`.

**combined_players.json path** — Set `COMBINED_PLAYERS_PATH` in `.env`
to point at the trade bot's data directory. In GitHub Actions this needs
to be handled differently — either commit a copy to this repo or fetch
it from the `fbp-trade-bot` repo as part of the workflow.

---

## Dependencies
```
pybaseball>=2.2.5       Baseball Savant / Statcast data
requests>=2.31.0        HTTP client
jinja2>=3.1.2           Email template rendering
sendgrid>=6.10.0        Email delivery
python-dotenv>=1.0.0    Local .env loading
anthropic>=0.20.0       Claude API (for AI features — not yet active)
```

---

*Built March 2026 by Zach Pressley with Claude (Anthropic).*
*For FBP league rules see the FBP Constitution 2026.*
*For questions about the trade bot or hub see their respective CLAUDE.md files.*