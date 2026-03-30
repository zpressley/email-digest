"""
Statcast leading indicator analysis for rostered hitters.
Shows trend direction (up/down) not sit/start recommendations.

Trend is determined by comparing recent 7-day metrics
against the 21-day baseline. Rising = positive trend.
"""
from src.data.statcast_client import StatcastClient
from src.data.yahoo_client import YahooClient
from src.config import STATCAST_ROLLING_DAYS

MIN_PA            = 15
TREND_UP_THRESHOLD   = 3.0   # signal score to show as trending up
TREND_DOWN_THRESHOLD = -2.0  # signal score to show as trending down


def get_statcast_trends() -> list[dict]:
    """
    Returns all rostered hitters with Statcast trend direction.
    Each player gets a trend: 'up', 'down', or 'neutral'.
    No bench recommendations — just directional signals.
    """
    yahoo    = YahooClient()
    statcast = StatcastClient()
    roster   = yahoo.get_my_roster()

    hitters = [
        p for p in roster
        if p.get("primary_position") not in ("SP", "RP", "P")
        and "SP" not in (p.get("eligible_positions") or [])
        and "RP" not in (p.get("eligible_positions") or [])
    ]

    print(f"  📊 Statcast: checking {len(hitters)} rostered hitters")

    trends = []
    for player in hitters:
        name   = player.get("name", "")
        mlb_id = statcast.get_mlb_id(name)
        if not mlb_id:
            continue

        metrics = statcast.get_hitter_metrics(name, mlb_id=mlb_id)
        if not metrics or metrics.get("pa", 0) < MIN_PA:
            continue

        score = _compute_signal_score(metrics)

        if score >= TREND_UP_THRESHOLD:
            trend = "up"
        elif score <= TREND_DOWN_THRESHOLD:
            trend = "down"
        else:
            trend = "neutral"

        trends.append({
            **metrics,
            "signal_score": round(score, 1),
            "trend":        trend,
        })

    # Sort: trending up first, then neutral, then down
    order = {"up": 0, "neutral": 1, "down": 2}
    trends.sort(key=lambda x: (order[x["trend"]], -x["signal_score"]))
    return trends


def get_breakout_watch() -> list[dict]:
    """Top hitters with strong positive Statcast signals."""
    return [t for t in get_statcast_trends() if t["trend"] == "up"][:5]


def get_bench_candidates() -> list[dict]:
    """
    Kept for backwards compatibility but returns empty list.
    Bench recommendations removed — use get_statcast_trends() instead.
    """
    return []


def _compute_signal_score(metrics: dict) -> float:
    """
    Composite signal score. Positive = bullish. Negative = bearish.
    """
    score  = 0.0
    barrel = metrics.get("barrel_rate")
    ev     = metrics.get("avg_exit_velocity")
    walk   = metrics.get("walk_rate")
    whiff  = metrics.get("whiff_rate")
    xba    = metrics.get("xba")
    ba     = metrics.get("ba")

    # Positive signals
    if barrel and barrel > 10:
        score += 2.0
    if ev and ev > 92:
        score += 1.5
    if walk and walk > 10:
        score += 1.0
    if whiff and whiff < 20:
        score += 1.0
    if xba and ba and (xba - ba) > 0.030:
        score += 2.0  # xBA >> BA = due for positive regression

    # Negative signals
    if whiff and whiff > 30:
        score -= 2.0
    elif whiff and whiff > 25:
        score -= 1.0
    if ev and ev < 86:
        score -= 2.0
    elif ev and ev < 88:
        score -= 1.0
    if barrel is not None and barrel < 3:
        score -= 1.5
    if xba and ba and (ba - xba) > 0.040:
        score -= 2.0  # BA >> xBA = overperforming, regression coming
    if walk is not None and walk < 4:
        score -= 0.5

    return score