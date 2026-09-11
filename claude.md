# email-digest — Project Documentation
*Last updated: September 2026 — Yahoo API removed; runs on pybaseball + MLB Stats API + Discord feed*

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
for the roster pickup lag.

Runs entirely on GitHub Actions. Zero hosting cost.

**September 2026 redesign:** the Yahoo Fantasy API dependency is gone.
League rosters come from `combined_players.json` (trade bot pipeline),
everything else from the free MLB Stats API, Baseball Savant (pybaseball),
and the TweetShift Twitter-dump channels on Discord. Email delivery moved
from the SendGrid SDK to plain HTTPS with Resend preferred and SendGrid
as fallback (the old SendGrid key had been 401ing for weeks and silently
killing every run at the send step).

---

## The Most Important Design Principle

**Roster lag awareness.** In this league, a pickup made today does not
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
│       └── daily_digest.yml        # Runs 6 AM CST daily
├── src/
│   ├── config.py                   # All env vars + league constants
│   ├── daily_digest.py             # Daily entrypoint
│   ├── data/
│   │   ├── league_rosters.py       # Rosters/FAs from combined_players.json
│   │   ├── mlb_client.py           # MLB Stats API (free, public)
│   │   ├── statcast_client.py      # Baseball Savant via pybaseball
│   │   ├── team_offense_ranker.py  # Rolling team offense ranks (MLB API)
│   │   ├── ai_client.py            # Claude Haiku (farm report + pulse)
│   │   └── snapshot_store.py       # Daily JSON snapshot persistence
│   ├── analysis/
│   │   ├── roster_analyzer.py      # Today's roster vs schedule
│   │   ├── pitching_planner.py     # My upcoming starts + streamer recs
│   │   ├── free_agent_tracker.py   # Hot unrostered hitters (last 7 days)
│   │   ├── hitter_analyzer.py      # Statcast breakout/bench signals
│   │   ├── category_standings.py   # Matchup status from data/standings.json
│   │   ├── discord_reader.py       # TweetShift Twitter-dump channels
│   │   └── prospect_tracker.py     # Minor league callouts
│   └── mailer/
│       ├── daily_template.html     # Jinja2 HTML email template
│       ├── renderer.py             # Jinja2 render functions
│       └── sender.py               # Resend (preferred) / SendGrid delivery
├── data/
│   ├── snapshots/                  # Daily JSON snapshots (git-tracked)
│   ├── statcast_snapshots/         # Statcast baselines for deltas
│   └── baselines/                  # Written by trade-bot pipeline
├── tests/
│   └── test_digest_offline.py      # Offline smoke test, mocks all HTTP
├── requirements.txt
├── .env.example
└── claude.md                       # This file
```

---

## How It Works — Daily Flow

At 6 AM CST, GitHub Actions runs `src/daily_digest.py`. Here is what
happens in order:

1. The workflow copies `config/managers.json` from the FBPTradeBot repo
   into `data/` (abbr ↔ team-name mapping for roster ownership).

2. Each analysis module is called in sequence, pulling rosters from
   `league_rosters` (combined_players.json) and stats from `MLBClient`
   / pybaseball independently.

3. The Discord reader pulls the last 24h of TweetShift posts from the
   Twitter-dump channels; Claude Haiku turns them into the Baseball Pulse.

4. All results are assembled into a single `context` dict.

5. The context is saved as a daily JSON snapshot to `data/snapshots/`.

6. Jinja2 renders `daily_template.html` with the context.

7. Resend (or SendGrid, as fallback) delivers the email to `TO_EMAIL`.

8. GitHub Actions commits the updated snapshot back to the repo.

Set `DIGEST_DRY_RUN=1` to skip sending and write `digest_preview.html`
instead (path overridable with `DIGEST_PREVIEW_PATH`).

---

## Data Sources

| Source | What It Provides | Auth |
|---|---|---|
| combined_players.json | League rosters, free agents, prospects, contracts, MLB IDs | Local file from trade bot |
| MLB Stats API | Schedule, probable starters, player stats, date-range leaders, minor league stats, team offense | None — free public API |
| Baseball Savant (pybaseball) | Statcast: barrel rate, whiff rate, chase rate, hard hit, xBA | None — scrapes public data |
| Discord (TweetShift) | Curated Twitter/X firehose for the AI Baseball Pulse | Bot token |
| data/standings.json | Matchup status + record (written by trade bot, optional) | Local file |
| Snapshot store | Day-over-day deltas, trend tracking | Local JSON files |

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
RESEND_API_KEY            Resend API key — preferred email provider
SENDGRID_API_KEY          SendGrid API key — fallback provider (optional)
TO_EMAIL                  Recipient email address
FROM_EMAIL                Sender address (must be a verified domain sender)
DISCORD_BOT_TOKEN         Bot token for the TweetShift feed channels
ANTHROPIC_KEY             Claude API key (farm report + baseball pulse)
COMBINED_PLAYERS_PATH     Path to combined_players.json from trade bot
MY_TEAM_ABBR              FBP team abbreviation (WAR)
```

No season-rollover updates needed anymore — there are no Yahoo league IDs
or game keys in this repo.

---

## League Rosters (`src/data/league_rosters.py`)

The Yahoo client's replacement. Reads `combined_players.json` and
`data/managers.json` (copied from FBPTradeBot by the workflow).

**Gotcha this module exists to solve:** the `manager` field in
combined_players.json holds full FBP team NAMES ("Weekend Warriors"),
not abbreviations ("WAR") — it held abbreviations historically.
`manager_matches(record, abbr)` accepts either, using the managers.json
abbr → name mapping with a hardcoded 2026 fallback. Every module that
filters by owner must go through this helper, never compare
`record["manager"] == "WAR"` directly.

Functions:

`get_my_roster()` — MY_TEAM_ABBR's MLB players, normalized to the shape
analyzers expect (name, primary_position, eligible_positions, mlb_team,
mlb_id, status).

`get_rostered_mlb_ids()` / `get_rostered_names()` — every player rostered
by ANY league team; used to identify free agents.

`is_pitcher(player)` — position-eligibility check (SP/RP/P).

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

Crosses my roster (from `league_rosters`) against today's MLB schedule.
Grades each hitter's matchup as favorable (opponent SP ERA ≥ 4.50),
neutral, or tough (ERA ≤ 3.25). Team abbreviation variants normalized
via `ABBR_ALIASES`.

### `pitching_planner.py`

MLB-schedule-driven replacement for the Yahoo weekly matchup engine.
Collects probable starters (with team abbreviations) over the next 7
days from the schedule endpoint, then:

**my_starts** — every probable start by one of my rostered pitchers
(matched by `mlb_id`, no name fuzzing), graded by opponent offense rank
from `team_offense_ranker` plus season ERA/K9.

**streamers** — starters rostered by NO league team, facing bottom-half
offenses (rank ≥ 16) inside the 5-day streaming window, season ERA ≤
5.00. Composite score = low ERA + weak opponent + lead time. Starts
inside `ROSTER_LAG_DAYS` are excluded (can't roster them in time);
`latest_add` / `add_today` flag the pickup deadline.

### `free_agent_tracker.py`

Yahoo ownership trends replaced with observed performance: pulls the
last-7-day hitting leaderboard (MLB Stats API `byDateRange` sorted by
OPS, with a pybaseball `batting_stats_range` fallback), filters out
everyone rostered in the league via `league_rosters`, and surfaces the
top 8 unrostered hitters with ≥ 12 PA in the window.

### `hitter_analyzer.py`

Wraps the Statcast client. `get_statcast_trends()` scores every rostered
hitter (roster from `league_rosters`, MLB IDs straight from
combined_players.json) and returns only meaningful up/down trends, with
sample-size gating and day-over-day deltas from the statcast snapshots.

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

### `category_standings.py`

Reads `data/standings.json` (written by the trade bot pipeline, if
present) for current record, rank, and matchup category score. The
section silently skips when the file is absent — no API involved.

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

Daily template sections in order: Current Matchup (conditional), My
Upcoming Starts, Streaming Targets, Today's Lineup, Free Agent Watch,
Statcast Signals, Farm System, Farm Report (AI), Baseball Pulse (AI).

Conditional sections only render if they contain data.

---

## GitHub Actions

### `daily_digest.yml`
Cron: `0 11 * * *` (6 AM CST = 11 AM UTC)

Steps: checkout → install deps → copy managers.json from FBPTradeBot →
run `daily_digest.py` → commit updated snapshots → push.

Branch note: If you add auto-deploy triggers, ensure snapshot commits
include `[skip ci]` in the message or use path filters to prevent
redeploys on every snapshot commit.

---

## Relationship to Other Repos

**`fbp-trade-bot`** — Discord bot + FastAPI backend. Runs the daily data
pipeline that writes `combined_players.json` (synced into this repo) and
`config/managers.json` (copied in by the workflow).

**`fbp-hub`** — GitHub Pages frontend at PantheonLeague.com. Hosts
`league_baselines.json` for the Chrome extension. This repo does not
depend on `fbp-hub`.

**`email-digest` (this repo)** — Standalone. Reads from the MLB Stats
API, Baseball Savant, Discord, and `combined_players.json` from the
trade bot. Writes daily snapshots back to itself only.

---

## League Context

FBP is a 12-team dynasty H2H categories league in its 14th season (2026).

**Hitting categories:** R, HR, RBI, SB, AVG, OBP, SLG, OPS, TB, NSB

**Pitching categories:** W, SV, K, ERA, WHIP, K9, BB9, QS, HLD, SVHD

Each team has a 26-man active roster plus 4 FBP Hub spots for
called-up prospects (the 30-man rule). 5 NA slots for minor leaguers.

Team abbreviations (see `data/managers.json` for the abbr ↔ team-name
mapping): WIZ, HAM, B2J, CFL, JEP, LFB, DMN, SAD, DRO, RV, TBB,
WAR (Zach — Weekend Warriors, set via `MY_TEAM_ABBR`).

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
```

**Preview without sending email:**
```bash
DIGEST_DRY_RUN=1 PYTHONPATH=. python3 src/daily_digest.py
open digest_preview.html
```

**Offline smoke test (no network, no credentials):**
```bash
PYTHONPATH=. python3 tests/test_digest_offline.py
```

---

## Known Gotchas

**Manager field holds team names** — `combined_players.json` stores
`"manager": "Weekend Warriors"`, not `"WAR"`. Always filter ownership
through `league_rosters.manager_matches()` — a direct abbr comparison
silently matches nothing (this bug blanked the farm report for months).

**Team abbreviation variants** — `team_offense_ranker.ABBR_ALIASES`
handles known mismatches: CWS/CHW, TB/TBR, SF/SFG, SDP/SD, KCR/KC,
WSN/WSH, ARI/AZ. Add new mismatches there if a team lookup misses.

**pybaseball rate limiting** — Baseball Savant throttles occasionally.
Client returns empty dict rather than crashing. If consistently empty,
add `time.sleep(1)` between player lookups.

**Email 401s are silent killers** — a revoked provider key fails only at
the very last step of the run, after every section built fine. The sender
raises with provider name + HTTP status; if the digest stops arriving,
check the tail of the Actions log first.

**Early season data gaps** — First 14 days of season, rolling offense
rankings have small samples. The offense ranker falls back to season
totals automatically. Treat early-season streaming scores with skepticism.

**FA leaderboard endpoint** — `free_agent_tracker` relies on the MLB
`/stats?stats=byDateRange&sortStat=...` leaderboard. If MLB ever changes
that endpoint, the pybaseball `batting_stats_range` fallback (Baseball-
Reference) takes over automatically; both failing just yields an empty
section, never a crash.

**mailer vs email folder** — The email template folder is named `mailer/`
not `email/`. This is intentional — Python 3.13 shadows the built-in
`email` module if a local folder has the same name, breaking `urllib3`.
Do not rename it back to `email/`.

**combined_players.json path** — Set `COMBINED_PLAYERS_PATH` in `.env`
to point at the trade bot's data directory locally. In GitHub Actions
the trade bot pipeline syncs a copy into this repo's `data/` directory
daily and the workflow points at that.

---

## Dependencies
```
pybaseball>=2.2.5       Baseball Savant / Statcast data + B-Ref fallback
requests>=2.31.0        HTTP client (MLB API, Discord, Resend/SendGrid)
jinja2>=3.1.2           Email template rendering
python-dotenv>=1.0.0    Local .env loading
anthropic>=0.49.0       Claude API (farm report + baseball pulse)
```

---

*Built March 2026 by Zach Pressley with Claude (Anthropic).*
*For FBP league rules see the FBP Constitution 2026.*
*For questions about the trade bot or hub see their respective CLAUDE.md files.*