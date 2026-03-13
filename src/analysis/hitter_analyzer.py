"""Statcast leading indicator analysis for rostered hitters."""
from src.data.statcast_client import StatcastClient
from src.data.yahoo_client import YahooClient


def get_breakout_watch() -> list[dict]:
    """Top hitters with strong positive Statcast signals."""
    raise NotImplementedError


def get_bench_candidates() -> list[dict]:
    """Rostered hitters with deteriorating Statcast signals."""
    raise NotImplementedError
