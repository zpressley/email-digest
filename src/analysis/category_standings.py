"""20-category standings with gap-to-first and gap-to-last."""
from src.data.yahoo_client import YahooClient
from src.data.snapshot_store import load_latest_snapshot, diff_snapshots
from src.config import LEAGUE_CATEGORIES_HITTING, LEAGUE_CATEGORIES_PITCHING


def get_category_dashboard() -> list[dict]:
    """
    All 20 categories with:
    - my_rank, my_total, gap_to_first, gap_to_last, week_over_week_trend
    Below-median categories flagged as acquisition targets.
    """
    raise NotImplementedError


def get_target_categories(dashboard: list[dict], top_n: int = 3) -> list[str]:
    """Returns N categories with smallest gap to moving up one rank."""
    raise NotImplementedError
