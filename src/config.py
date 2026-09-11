import os
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
TO_EMAIL = os.getenv("TO_EMAIL")
FROM_EMAIL = os.getenv("FROM_EMAIL", "digest@fantasy.local")

LEAGUE_CATEGORIES_HITTING = [
    "R", "HR", "RBI", "SB", "AVG", "OBP", "SLG", "OPS", "TB", "NSB"
]
LEAGUE_CATEGORIES_PITCHING = [
    "W", "SV", "K", "ERA", "WHIP", "K9", "BB9", "QS", "HLD", "SVHD"
]
LEAGUE_SIZE = 12

ROSTER_LAG_DAYS = 1
STREAMING_WINDOW_DAYS = 5
FA_OWNERSHIP_THRESHOLD = 30.0
STATCAST_ROLLING_DAYS = 21
SNAPSHOT_DIR = os.getenv("SNAPSHOT_DIR", "data/snapshots")
COMBINED_PLAYERS_PATH = os.getenv(
    "COMBINED_PLAYERS_PATH",
    "/Users/zpressley/fbp-trade-bot/data/combined_players.json"
)
MY_TEAM_ABBR = os.getenv("MY_TEAM_ABBR", "WAR")
