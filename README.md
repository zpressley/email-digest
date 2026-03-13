# email-digest

Personal fantasy baseball morning briefing. Delivers a daily email at 8 AM with
roster impact, streaming opportunities, free agent heat, Statcast signals, and
prospect callouts. Weekly review every Sunday with category standings analysis.

## Setup

1. Clone repo
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in credentials
4. Port Yahoo OAuth client from trade bot into `src/data/yahoo_client.py`
5. Add GitHub repository secrets matching `.env.example`

## Running locally
```bash
python src/daily_digest.py
python src/weekly_review.py
```

## Roster Lag

All forward-looking alerts account for the 1-day pickup lag.
Streaming opportunities only surface starts 2+ days out.
