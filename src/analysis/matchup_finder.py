"""Streaming opportunity finder — pitcher vs. weak offense."""
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.config import FA_OWNERSHIP_THRESHOLD, STREAMING_WINDOW_DAYS, ROSTER_LAG_DAYS


def get_streaming_opportunities() -> list[dict]:
    """
    Finds unowned/low-owned pitchers facing weak offenses within pickup window.
    Returns ranked list by composite score.
    Only returns starts >= ROSTER_LAG_DAYS out.
    """
    raise NotImplementedError


def score_opportunity(pitcher: dict, opponent_rank: int, days_out: int) -> float:
    """Composite score. Higher = better streaming play."""
    pitcher_score = max(0, (4.5 - pitcher.get("era", 4.5)) * 10)
    opponent_score = max(0, (12 - opponent_rank) * 5)
    timing_score = max(0, (STREAMING_WINDOW_DAYS - days_out + 1) * 2)
    return round(pitcher_score + opponent_score + timing_score, 2)
