"""Pitcher start analysis — upcoming starts and probable starter logic."""
from datetime import date, timedelta
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.config import ROSTER_LAG_DAYS


def get_my_upcoming_starts(days_ahead: int = 5) -> list[dict]:
    """
    Returns my rostered pitchers with confirmed or probable starts
    in the next N days. Flags starts within ROSTER_LAG_DAYS as 'act now'.
    """
    raise NotImplementedError


def get_league_pitcher_usage() -> list[dict]:
    """
    Aggregates pitcher deployment across all 12 teams:
    avg starts, avg RP appearances, workload extremes.
    """
    raise NotImplementedError


def get_pitcher_last_start_date(player_id: int) -> date | None:
    """Returns date of the pitcher's most recent start."""
    raise NotImplementedError
