"""Minor league and recent call-up performance tracker."""
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient


def get_prospect_callouts() -> list[dict]:
    """
    For minors-rostered players and recent call-ups:
    - strong/poor recent performance flags
    - call-up news
    Returns empty list if nothing notable.
    """
    raise NotImplementedError
