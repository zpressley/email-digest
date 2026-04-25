# email_digest — Project Rules

## Coding principles — Karpathy guidelines
Behavioral guidelines to reduce common LLM coding mistakes. Bias toward caution over speed; for trivial tasks, use judgment.

### 1. Think before coding
Don't assume. Don't hide confusion. Surface tradeoffs.
Before implementing:
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity first
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
Ask: would a senior engineer say this is overcomplicated? If yes, simplify.

### 3. Surgical changes
Touch only what you must. Clean up only your own mess.
When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.
The test: every changed line should trace directly to the user's request.

### 4. Goal-driven execution
Define success criteria. Loop until verified.
Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan with verify steps. Strong success criteria let you loop independently; weak criteria ("make it work") require constant clarification.

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
