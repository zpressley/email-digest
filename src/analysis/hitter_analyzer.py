"""Statcast leading indicator analysis for rostered hitters."""
from src.data.statcast_client import StatcastClient
from src.data.yahoo_client import YahooClient

BREAKOUT_SIGNAL_THRESHOLD = 3.0
BENCH_SIGNAL_THRESHOLD    = -2.0
MIN_PA                    = 15


def get_breakout_watch() -> list[dict]:
    """Top rostered hitters with strong positive Statcast signals."""
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

    signals = statcast.get_breakout_signals(hitters)
    return [
        s for s in signals
        if s.get("pa", 0) >= MIN_PA
        and s.get("signal_score", 0) >= BREAKOUT_SIGNAL_THRESHOLD
    ][:5]


def get_bench_candidates() -> list[dict]:
    """Rostered hitters with deteriorating Statcast signals."""
    yahoo    = YahooClient()
    statcast = StatcastClient()
    roster   = yahoo.get_my_roster()

    hitters = [
        p for p in roster
        if p.get("primary_position") not in ("SP", "RP", "P")
        and "SP" not in (p.get("eligible_positions") or [])
        and "RP" not in (p.get("eligible_positions") or [])
    ]

    bench = []
    for player in hitters:
        name   = player.get("name", "")
        mlb_id = statcast.get_mlb_id(name)
        if not mlb_id:
            continue

        metrics = statcast.get_hitter_metrics(name, mlb_id=mlb_id)
        if not metrics or metrics.get("pa", 0) < MIN_PA:
            continue

        score = _compute_negative_score(metrics)
        if score <= BENCH_SIGNAL_THRESHOLD:
            bench.append({**metrics, "signal_score": score})

    bench.sort(key=lambda x: x["signal_score"])
    return bench[:3]


def _compute_negative_score(metrics: dict) -> float:
    score  = 0.0
    whiff  = metrics.get("whiff_rate")
    ev     = metrics.get("avg_exit_velocity")
    barrel = metrics.get("barrel_rate")
    xba    = metrics.get("xba")
    ba     = metrics.get("ba")
    walk   = metrics.get("walk_rate")

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
        score -= 2.0  # BA well above xBA = due for negative regression
    if walk is not None and walk < 4:
        score -= 0.5

    return score