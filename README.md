# email-digest

Personal fantasy baseball morning briefing, daily at 6 AM CST. Two modes
via `DIGEST_MODE`:

- **offseason** (default) — the Hot Stove Digest: the Discord Twitter dump
  goes to Claude Opus for an offseason briefing on prospect development
  and 2027 fantasy outlooks, personalized to the WAR roster and farm.
- **inseason** — the full daily digest: roster impact, upcoming starts,
  streaming targets, free agent heat, Statcast signals, prospect
  callouts, and an AI news pulse.

No fantasy-host API. Data comes from:

- **combined_players.json** (trade bot pipeline) — league rosters, prospects, MLB IDs
- **MLB Stats API** — schedule, probable starters, stats, team offense
- **Baseball Savant via pybaseball** — Statcast leading indicators
- **Discord Twitter dump** (TweetShift channels) — news feed for the AI pulse

## Setup

1. Clone repo
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in credentials
4. Add GitHub repository secrets (`RESEND_API_KEY` or `SENDGRID_API_KEY`,
   `TO_EMAIL`, `FROM_EMAIL`, `DISCORD_BOT_TOKEN`, `ANTHROPIC_KEY`, `GH_PAT`)

## Running locally

```bash
PYTHONPATH=. python src/daily_digest.py                  # sends email
DIGEST_DRY_RUN=1 PYTHONPATH=. python src/daily_digest.py # writes digest_preview.html
PYTHONPATH=. python tests/test_digest_offline.py          # offline smoke test
```

## Roster Lag

All forward-looking alerts account for the 1-day pickup lag.
Streaming targets never surface starts you can't roster in time.
