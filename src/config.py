import os
from dotenv import load_dotenv

load_dotenv()

YAHOO_TOKEN_PATH = os.getenv("YAHOO_TOKEN_PATH", "./token.json")
YAHOO_CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
YAHOO_CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
YAHOO_LEAGUE_ID = os.getenv("YAHOO_LEAGUE_ID")
YAHOO_GAME_KEY = os.getenv("YAHOO_GAME_KEY", "469")  # 469 = MLB 2026
YAHOO_TEAM_ID = os.getenv("YAHOO_TEAM_ID")  # your team number (1–12)

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
SNAPSHOT_DIR = "data/snapshots"
