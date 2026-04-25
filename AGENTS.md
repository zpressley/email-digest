# email_digest — Project Rules

## Coding principles (Karpathy, distilled)
- Think before coding.
- Simplicity first.
- Surgical edits only.
- Goal-driven targets before starting.

## Team identity
- The user's fantasy team is **WAR (Weekend Warriors)**, Yahoo team_id **12**
- WAR is always the "my team" side in all digest output, scorecard headers, and scoring logic
- The team was previously called "WIZ" — that name is retired; never use it
- All other teams in the 12-team league are opponents; WAR is never an opponent of itself

## Key constants
- `YAHOO_TEAM_ID = "12"` — hardcoded default in `src/config.py`; the GitHub Actions secret must also be `12`
- `MY_TEAM_ABBR = "WAR"`
- `YAHOO_LEAGUE_ID = "8560"`, `YAHOO_GAME_KEY = "469"` (MLB 2026)
- Yahoo week runs Monday–Sunday
- IP minimum threshold: 35 IP per week

## Codebase layout
- `src/data/` — API clients and projection engine (`yahoo_client.py`, `mlb_client.py`, `weekly_matchup_engine.py`, `statcast_client.py`)
- `src/analysis/` — per-feature analysis modules
- `src/mailer/` — HTML template, CSS, renderer, sender
- `src/daily_digest.py` — main entrypoint
- `data/` — runtime data files (`combined_players.json`, `pitcher_logs/` cache, `snapshots/`)

## Code conventions
- Never put backslash escapes (`\uXXXX`, `\n`, etc.) inside f-string `{}` expression blocks — pre-compute to a variable first
- Stat key naming: `K` = pitching strikeouts, `K_hit` = batting strikeouts, `HR` = HR allowed (pitching), `HR_hit` = batting home runs
- ERA/K9/H9/BB9 are always derived from raw components — never fetched directly from Yahoo
- Before large changes, notify the user and get confirmation
