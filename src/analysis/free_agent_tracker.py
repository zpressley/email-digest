"""Free agent heat index — who's trending and when to add them."""
from datetime import date, timedelta
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.config import FA_OWNERSHIP_THRESHOLD, ROSTER_LAG_DAYS


def get_hot_free_agents() -> list[dict]:
    """
    Returns trending free agents with:
    - name, position, ownership %, trend direction
    - latest_add_date: last day to pick up in time for next key game
    """
    yahoo = YahooClient()
    mlb = MLBClient()

    trending = yahoo.get_ownership_trends()
    probable_starters = mlb.get_probable_starters(days_ahead=5)

    # Build a lookup: player name → soonest start date
    starter_lookup: dict[str, str] = {}
    for s in probable_starters:
        name_lower = (s.get("name") or "").lower()
        existing = starter_lookup.get(name_lower)
        if not existing or s["game_date"] < existing:
            starter_lookup[name_lower] = s["game_date"]

    hot = []
    for player in trending:
        ownership = player.get("ownership", 0.0)
        trend_str = player.get("trend", "—")

        # Only surface players below ownership threshold with positive trend
        if ownership >= FA_OWNERSHIP_THRESHOLD:
            continue
        try:
            trend_val = float(trend_str.replace("%", "").replace("+", ""))
        except (ValueError, AttributeError):
            trend_val = 0.0

        if trend_val <= 0:
            continue

        name = player.get("name", "")
        position = player.get("position", "")
        name_lower = name.lower()

        # Find their next relevant game and work back by ROSTER_LAG_DAYS
        next_game = starter_lookup.get(name_lower)
        if next_game:
            next_game_date = date.fromisoformat(next_game)
            latest_add = next_game_date - timedelta(days=ROSTER_LAG_DAYS)
        else:
            # No confirmed start — add window is open-ended, flag as this week
            latest_add = date.today() + timedelta(days=3)

        days_until_deadline = (latest_add - date.today()).days
        urgent = days_until_deadline <= 1

        hot.append({
            "name": name,
            "position": position,
            "ownership": ownership,
            "trend": trend_str,
            "trend_val": trend_val,
            "latest_add_date": latest_add.strftime("%A"),
            "urgent": urgent,
            "has_start": next_game is not None,
            "next_game": next_game,
        })

    # Sort by trend value descending — fastest rising first
    hot.sort(key=lambda x: x["trend_val"], reverse=True)
    return hot[:10]  # top 10 only
