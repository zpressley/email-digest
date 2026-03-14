"""Statcast leading indicator analysis for rostered hitters."""
from src.data.statcast_client import StatcastClient
from src.data.yahoo_client import YahooClient


# Thresholds for positive signals
BREAKOUT_SIGNAL_THRESHOLD = 3.0
BENCH_SIGNAL_THRESHOLD = -2.0

# Minimum PA to include in analysis (avoid small sample noise)
MIN_PA = 15


def get_breakout_watch() -> list[dict]:
    """
    Returns top rostered hitters with strong positive Statcast signals.
    Ordered by signal score descending.
    """
    yahoo = YahooClient()
    statcast = StatcastClient()

    roster = yahoo.get_my_roster()
    hitters = [
        p for p in roster
        if p.get("primary_position") not in ("SP", "RP", "P")
        and "SP" not in (p.get("eligible_positions") or [])
        and "RP" not in (p.get("eligible_positions") or [])
    ]

    signals = statcast.get_breakout_signals(hitters)

    # Filter to meaningful sample size and positive signal
    breakouts = [
        s for s in signals
        if s.get("pa", 0) >= MIN_PA
        and s.get("signal_score", 0) >= BREAKOUT_SIGNAL_THRESHOLD
    ]

    return breakouts[:5]  # top 5 breakout candidates


def get_bench_candidates() -> list[dict]:
    """
    Returns rostered hitters with deteriorating Statcast signals.
    These are players worth benching or monitoring closely.
    """
    yahoo = YahooClient()
    statcast = StatcastClient()

    roster = yahoo.get_my_roster()
    hitters = [
        p for p in roster
        if p.get("primary_position") not in ("SP", "RP", "P")
        and "SP" not in (p.get("eligible_positions") or [])
        and "RP" not in (p.get("eligible_positions") or [])
    ]

    bench = []
    for player in hitters:
        metrics = statcast.get_hitter_metrics(
            player.get("first_name", ""),
            player.get("last_name", ""),
        )
        if not metrics or metrics.get("pa", 0) < MIN_PA:
            continue

        score = _compute_negative_score(metrics)
        if score <= BENCH_SIGNAL_THRESHOLD:
            bench.append({
                **metrics,
                "signal_score": score,
            })

    bench.sort(key=lambda x: x["signal_score"])  # worst first
    return bench[:3]  # surface top 3 concerns only


def _compute_negative_score(metrics: dict) -> float:
    """
    Negative composite score for deteriorating hitters.
    More negative = more concerning.
    """
    score = 0.0

    # High whiff rate = poor contact
    if metrics.get("whiff_rate") and metrics["whiff_rate"] > 30:
        score -= 2.0
    elif metrics.get("whiff_rate") and metrics["whiff_rate"] > 25:
        score -= 1.0

    # Low exit velocity = weak contact
    if metrics.get("avg_exit_velocity") and metrics["avg_exit_velocity"] < 86:
        score -= 2.0
    elif metrics.get("avg_exit_velocity") and metrics["avg_exit_velocity"] < 88:
        score -= 1.0

    # Low barrel rate = no power
    if metrics.get("barrel_rate") is not None and metrics["barrel_rate"] < 3:
        score -= 1.5

    # xBA well below actual BA = due for downward regression
    if metrics.get("xba") and metrics.get("ba"):
        gap = metrics.get("ba", 0) - metrics["xba"]
        if gap > 0.040:
            score -= 2.0  # overperforming, regression coming

    # Low walk rate = no plate discipline
    if metrics.get("walk_rate") is not None and metrics["walk_rate"] < 4:
        score -= 0.5

    return score
