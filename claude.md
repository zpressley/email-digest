# email-digest — Project Documentation
*Last updated: March 2026*

---

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
│   └── email/
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
| Snapshot store | Week-over-week deltas, category trend tracking | Local JSON files |

---

## Environment Variables

Copy `.env.example` to `.env` for local development. For GitHub Actions, 
add these as repository secrets.
```
YAHOO_TOKEN_PATH     Path to token.json (default: ./token.json)
YAHOO_CLIENT_ID      Yahoo app client ID
YAHOO_CLIENT_SECRET  Yahoo app client secret
YAHOO_LEAGUE_ID      FBP league ID (currently 15505 — verify each season)
YAHOO_GAME_KEY       MLB season game key (2026 = 469)
YAHOO_TEAM_ID        Your team number in the league (1–12)
SENDGRID_API_KEY     SendGrid API key for email delivery
TO_EMAIL             Recipient email address
FROM_EMAIL           Sender address (default: digest@fantasy.local)
```

**Important:** `YAHOO_TOKEN_PATH` should point to the same `token.json` 
used by the trade bot. In GitHub Actions, the workflow writes the token 
from the `YAHOO_TOKEN_JSON` secret at runtime.

---

## The Yahoo Client

`src/data/yahoo_client.py` is the most critical file in the project. It 
handles all communication with the Yahoo Fantasy API.

**Key design decisions:**

- Shares `token.json` with the trade bot. When the token refreshes here 
  it writes back to the same file so both repos always have a valid token.
- Uses the 2026 game key `469` — this must be updated each season. Do not 
  use the generic `"mlb"` string, it does not work reliably.
- All Yahoo API responses are XML. The client parses them with 
  `xml.etree.ElementTree` and returns clean Python dicts.
- `YAHOO_TEAM_MAP` at the top of the file maps Yahoo numeric team IDs to 
  FBP abbreviations. This must match the trade bot's mapping exactly.

**Methods:**

`get_my_roster()` — Returns all players on my active roster as a list of 
dicts. Each dict includes name, position, eligible positions, MLB team, 
status, and injury note.

`get_league_standings()` — Returns all 12 teams with their current 
category stat totals keyed by Yahoo stat ID.

`get_free_agents(position, limit)` — Returns available free agents 
filtered by position. Includes ownership percentage.

`get_ownership_trends()` — Returns players sorted by adds in the last 48 
hours. This powers the free agent heat index.

`get_all_team_rosters()` — Returns every team's full roster. Used by the 
weekly pitcher usage report.

---

## The MLB Client

`src/data/mlb_client.py` wraps the MLB Stats API at 
`https://statsapi.mlb.com/api/v1`. No authentication required.

**Key methods:**

`get_schedule(target_date)` — Returns all games for a given date with 
probable pitchers and lineups hydrated.

`get_probable_starters(days_ahead)` — Calls `get_schedule` for each day 
in the window and extracts confirmed probable starters. Returns a flat 
list with `days_out` field.

`get_player_recent_stats(player_id, days)` — Rolling stats for any player 
over the last N days using the `byDateRange` stats endpoint.

`get_minor_league_stats(player_id)` — Minor league stats using sport IDs 
11 (AAA), 12 (AA), 13 (A+), 14 (A).

`get_team_offense_rankings(days)` — Returns teams ranked by runs scored 
over a rolling window. Used by the matchup finder. Falls back to season 
totals if the date range query returns empty.

---

## The Statcast Client

`src/data/statcast_client.py` uses the `pybaseball` library to pull 
Statcast data from Baseball Savant.

It tracks these leading indicators for hitters:

- **Barrel rate** — rising barrel rate predicts incoming power before it 
  shows in box scores
- **Average exit velocity** — quality of contact trending up
- **xBA vs actual BA gap** — if xBA is significantly above BA, the hitter 
  is due for positive regression
- **Whiff rate** — falling whiff rate means improving contact
- **Walk rate** — rising walk rate signals better plate discipline

`get_breakout_signals(roster)` iterates over a roster list and scores each 
hitter using `_compute_signal_score()`. A score above 3.0 is surfaced as 
a breakout candidate. A score below -2.0 is surfaced as a bench candidate.

**Note:** pybaseball queries can be slow (3–8 seconds per player). For a 
full roster of 15 hitters this adds roughly 60–90 seconds to the run time. 
This is acceptable for a 6 AM cron job.

---

## Analysis Modules

### `roster_analyzer.py`

Crosses my Yahoo roster against today's MLB schedule. For each of my 
hitters with a game, it fetches the opponent's probable starter ERA and 
grades the matchup as favorable (ERA ≥ 4.50), neutral, or tough 
(ERA ≤ 3.25). Results are sorted favorable-first.

### `pitcher_analyzer.py`

`get_my_upcoming_starts()` — Matches my rostered pitchers against 
`mlb_client.get_probable_starters()` using last-name fuzzy matching. 
Returns starts sorted by soonest first. Any start within `ROSTER_LAG_DAYS` 
is flagged `act_now: True`.

`get_league_pitcher_usage()` — Pulls all 12 team rosters and counts SPs 
vs RPs per team. Flags extremes: heavy SP load (6+ starters), bullpen 
heavy (6+ relievers), low usage (3 or fewer starters). Used in the weekly 
review.

### `matchup_finder.py`

The most complex analysis module. Finds unowned or low-owned SPs (under 
`FA_OWNERSHIP_THRESHOLD` = 30%) facing offensively weak teams within the 
5-day streaming window, excluding starts within `ROSTER_LAG_DAYS`.

Composite score formula:
- `pitcher_score` = (5.00 - ERA) × 12, rewards low ERA
- `opponent_score` = (opponent_rank - 10) × 4, rewards worse offenses
- `timing_score` = (window - days_out + 1) × 2, rewards starts further 
  out (more time to add)

Skips any pitcher with ERA above 5.50 regardless of matchup. Returns top 
6 opportunities sorted by score.

`_get_team_offense_rankings()` hits the MLB Stats API for rolling 14-day 
team run totals. Falls back to season totals via `_season_offense_rankings()` 
if the date range returns empty (common early in the season).

### `free_agent_tracker.py`

Calls `yahoo_client.get_ownership_trends()` which returns players sorted 
by 48-hour adds. Filters to players under 30% owned with a positive trend 
value. For each player, checks if they have a confirmed start in the 
probable starters data and calculates the `latest_add_date` as game date 
minus `ROSTER_LAG_DAYS`. Returns top 10 rising players.

### `hitter_analyzer.py`

Wraps the Statcast client. `get_breakout_watch()` filters my roster to 
hitters only (non-SP/RP), calls `statcast_client.get_breakout_signals()`, 
and returns the top 5 with signal score ≥ 3.0 and at least 15 PA in the 
window.

`get_bench_candidates()` uses `_compute_negative_score()` which penalizes 
high whiff rate (>30%), low exit velocity (<86 mph), low barrel rate 
(<3%), BA significantly above xBA (due for downward regression), and low 
walk rate (<4%). Returns top 3 most concerning hitters.

### `prospect_tracker.py`

Iterates over my roster looking for players with a non-active status flag 
(NA, DL, IL) which indicates minor league assignment. For each, attempts 
to resolve an MLB ID from the Yahoo player ID, then fetches minor league 
stats from the MLB Stats API. Evaluates batters against OPS and AVG 
thresholds and pitchers against ERA thresholds. Flags call-up watch 
candidates (OPS ≥ 1.000 or ERA ≤ 1.50).

### `category_standings.py` (weekly only)

Pulls league standings from Yahoo and maps stat IDs to the 20 FBP 
categories. Computes rank, gap to first, gap to last, and week-over-week 
trend by diffing against last week's snapshot. Categories below the league 
median are flagged as acquisition targets.

---

## Snapshot Store

`src/data/snapshot_store.py` saves a JSON file per day to 
`data/snapshots/YYYY-MM-DD.json`. These are committed back to the repo by 
the GitHub Actions workflow after each run.

`diff_snapshots(current, previous, key)` produces week-over-week deltas 
for any numeric key — used by the weekly category standings to show trend 
arrows.

`load_latest_snapshot()` walks back up to 7 days to find the most recent 
available snapshot. This handles weekends and gaps gracefully.

---

## Email Templates

Both templates live in `src/email/` and are rendered with Jinja2. They use 
inline CSS only — no external stylesheets — for maximum Gmail 
compatibility.

The daily template renders these sections in order: Today's Roster Impact, 
Upcoming Pitcher Starts, Streaming Opportunities, Free Agent Heat Index, 
Statcast Breakout Watch, Consider Benching (conditional), Prospect Callouts 
(conditional).

Sections marked conditional only render if they contain data. This keeps 
the email clean on quiet days.

---

## GitHub Actions

### `daily_digest.yml`

Cron: `0 11 * * *` (6 AM CST = 11 AM UTC)

Steps: checkout repo → install dependencies → write `token.json` from 
secret → run `daily_digest.py` → commit updated snapshots → push.

### `weekly_review.yml`

Cron: `0 11 * * 0` (6 AM CST Sunday)

Same steps but runs `weekly_review.py` and commits a weekly snapshot.

**Branch note:** Both workflows run on `main`. If you ever add auto-deploy 
triggers to this repo, make sure snapshot commits do not trigger 
redeploys — add `[skip ci]` to the commit message or use path filters.

---

## Relationship to Other Repos

This repo is one of three in the FBP ecosystem:

**`fbp-trade-bot`** — Discord bot + FastAPI backend. Handles trades, 
roster commands, standings, and the daily data pipeline. Contains 
`token_manager.py` (OAuth2 auth) and `calculate_baselines.py` (writes 
baseline stats used by the Chrome extension). The `token.json` this repo 
uses is shared with the trade bot — whichever runs last writes the 
refreshed token.

**`fbp-hub`** — GitHub Pages frontend at PantheonLeague.com. Also hosts 
`league_baselines.json` which the Chrome extension reads via raw GitHub 
URL. This repo does not depend on `fbp-hub` at all.

**`email-digest` (this repo)** — Standalone. Reads from Yahoo and MLB 
APIs at runtime. Writes daily snapshots back to itself. Does not write to 
either of the other repos.

The only shared resource is `token.json`. Keep `YAHOO_TOKEN_PATH` pointing 
to the same file across both the trade bot and this repo when running 
locally.

---

## League Context

FBP is a 12-team dynasty H2H categories league. Scoring is based on 20 
categories: 10 hitting, 10 pitching.

Hitting categories: R, HR, RBI, SB, AVG, OBP, SLG, OPS, TB, NSB

Pitching categories: W, SV, K, ERA, WHIP, K9, BB9, QS, HLD, SVHD

Each team has a 26-man active Yahoo roster plus 4 FBP Hub spots for 
called-up prospects not yet on the Yahoo roster (the 30-man rule). The 
5 NA slots are used for minor league prospects. IL slots are available 
for injured players.

Yahoo league ID: `15505` — verify at the start of each season.
Yahoo game key: `469` for 2026 — this changes every season. Update 
`YAHOO_GAME_KEY` in `.env` and GitHub secrets each March.

Team abbreviations: WIZ (Zach), HAM, B2J, CFL, JEP, LFB, LAW, SAD, 
DRO, RV, TBB, WAR.

---

## Running Locally
```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in env vars
cp .env.example .env

# Point YAHOO_TOKEN_PATH at the trade bot token.json
# e.g. YAHOO_TOKEN_PATH=/Users/zpressley/fbp-trade-bot/token.json

# Run daily digest
python src/daily_digest.py

# Run weekly review
python src/weekly_review.py
```

**TEST_MODE tip:** Before sending real emails, temporarily replace the 
`send_email()` call in `daily_digest.py` with a local HTML file write:
```python
with open("digest_preview.html", "w") as f:
    f.write(html)
print("Saved to digest_preview.html")
```

Open `digest_preview.html` in a browser to verify the output before 
wiring up SendGrid.

---

## What Is Not Built Yet

The following features are planned but not yet implemented:

**AI Farm System Report** — Use Claude API (Haiku model) to write a 
2–3 sentence narrative for each prospect in the system based on their 
minor league stats. Group by hot/cold/watch. Add to `daily_digest.py` 
context as `farm_report`. Estimated cost: ~$0.001 per digest.

**Baseball Pulse (Twitter/X summary)** — Set up IFTTT to pipe curated 
baseball Twitter accounts into a `#twitter-feed` Discord channel. The 
digest bot reads that channel via Discord API, passes the last 24 hours 
of posts to Claude API, and returns a 3–4 paragraph narrative summary. 
The Discord bot already has the API patterns needed — this is a ~2 hour 
build.

**Weekly category standings** — `category_standings.py` is stubbed. 
Needs Yahoo stat ID mapping for all 20 FBP categories implemented.

**Full weekly review** — `weekly_review.py` entrypoint is wired but the 
category dashboard and pitcher usage sections need the above stubs filled.

---

## Known Gotchas

**Yahoo team abbreviations vs MLB abbreviations** — Yahoo and the MLB 
Stats API sometimes use different abbreviations for the same team. 
`roster_analyzer.py` has a `_fuzzy_team_match()` function with a manual 
map for the known mismatches (CWS/CHW, TB/TBR, SF/SFG, etc.). If roster 
impact is missing players for a specific team, check this map first.

**pybaseball rate limiting** — Baseball Savant will occasionally throttle 
requests. The Statcast client will return an empty dict for that player 
rather than crashing. If you see consistently empty Statcast results, add 
a `time.sleep(1)` between player lookups in `get_breakout_signals()`.

**Early season data gaps** — For the first 14 days of the season, the 
rolling offense rankings will have very small samples and may not be 
meaningful. The matchup finder falls back to season totals automatically 
but treat early-season streaming scores with appropriate skepticism.

**Yahoo stat IDs change** — The numeric stat IDs Yahoo uses in standings 
responses are not the same as the category names. When implementing 
`category_standings.py`, you will need to map Yahoo's IDs to the 20 FBP 
category names. Hit the league stats settings endpoint to get the current 
mapping: `GET /fantasy/v2/league/{league_key}/settings`

**Token expiry during long runs** — The Yahoo access token expires every 
hour. `YahooClient.authenticate()` checks expiry before every request and 
refreshes proactively with a 60-second buffer. If you see 401 errors the 
client also retries once with a forced refresh.

---

## Dependencies
```
pybaseball>=2.2.5       Baseball Savant / Statcast data
yahoo-fantasy-api>=1.9.0  (reference only — we use raw requests)
requests>=2.31.0        HTTP client
jinja2>=3.1.2           Email template rendering
sendgrid>=6.10.0        Email delivery
python-dotenv>=1.0.0    Local .env loading
```

---

*Built in March 2026 by Zach Pressley with Claude (Anthropic).*
*For questions about league rules, see the FBP Constitution 2026.*