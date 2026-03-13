"""Free agent heat index — who's trending and when to add them."""
from src.data.yahoo_client import YahooClient
from src.config import FA_OWNERSHIP_THRESHOLD, ROSTER_LAG_DAYS
from datetime import date, timedelta


def get_hot_free_agents() -> list[dict]:
    """
    Returns trending free agents with:
    - name, position, ownership %, trend direction
    - latest_add_date: last day to pick up in time for next key game
    """
    raise NotImplementedError
