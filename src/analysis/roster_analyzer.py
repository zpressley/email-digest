"""Analyzes my active roster against today's schedule."""
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient


def get_todays_roster_impact() -> list[dict]:
    """
    Returns rostered players with games today, enriched with:
    - opponent, game time, ballpark
    - starting pitcher they face (name, hand, ERA)
    - favorable / unfavorable matchup flag
    """
    # TODO: implement
    raise NotImplementedError
