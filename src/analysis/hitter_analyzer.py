"""
Statcast trend analysis for rostered hitters.

Shows trend direction (↑ up / ↓ down) based on research-backed
sample size thresholds. No trends are shown until minimum sample
sizes are met — early April means this section will often be sparse
or empty. That is correct behavior.

Trend logic:
    Plate discipline (whiff rate, chase rate) — usable at 80 PA
    Contact quality (barrel rate, hard hit)   — usable at 50 BBE
    Expected stats (xBA)                      — usable at 200 PA

Signal score:
    Positive score = bullish signal → ↑ TRENDING UP
    Negative score = bearish signal → ↓ TRENDING DOWN
    Near zero      = not shown (filtered out before template)

Display rules:
    - Never show neutral trends — only up or down
    - Show sample size context so the reader knows how much to trust it
    - Early season: plate discipline metrics available before contact metrics
    - If only discipline metrics available, still show with caveat
"""
from src.data.statcast_client import (
    StatcastClient,
    MIN_PA_DISCIPLINE,
    MIN_BBE_CONTACT,
    MIN_PA_EXPECTED,
)
from src.data.yahoo_client import YahooClient

# Score thresholds for showing a trend
SHOW_UP_THRESHOLD   =  2.5   # must score >= this to show as trending up
SHOW_DOWN_THRESHOLD = -2.0   # must score <= this to show as trending down

# League average benchmarks (2024-2025 MLB averages)
LEAGUE_AVG_WHIFF    = 24.0   # league avg whiff rate
LEAGUE_AVG_CHASE    = 29.5   # league avg chase rate (O-Swing%)
LEAGUE_AVG_BARREL   =  7.5   # league avg barrel rate (Brls/BBE%)
LEAGUE_AVG_HARDHIT  = 38.0   # league avg hard hit rate


def get_statcast_trends() -> list[dict]:
    """
    Returns rostered hitters with meaningful Statcast trends.

    Only players who have crossed at least one sample size threshold
    are returned. Players with insufficient data are silently skipped.
    Neutral trends are filtered out — only up and down are returned.

    Sorted: up trends first (highest score), then down trends.
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

    results = []

    for player in hitters:
        name   = player.get("name", "")
        mlb_id = statcast.get_mlb_id(name)
        if not mlb_id:
            continue

        metrics = statcast.get_hitter_metrics(name, mlb_id=mlb_id)

        # get_hitter_metrics returns None if below all thresholds
        if not metrics or metrics.get("insufficient_data"):
            continue

        score = _compute_signal_score(metrics)
        trend = _classify_trend(score)

        # Skip neutral trends entirely
        if trend == "neutral":
            continue

        # Build display summary — only show metrics that have enough data
        display = _build_display(metrics, score, trend)
        results.append(display)

    # Sort: up first (highest score), then down (most negative score last)
    results.sort(key=lambda x: -x["signal_score"] if x["trend"] == "up" else x["signal_score"])
    return results


def get_breakout_watch() -> list[dict]:
    """Players with strong positive Statcast signals."""
    return [p for p in get_statcast_trends() if p["trend"] == "up"][:5]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _compute_signal_score(metrics: dict) -> float:
    """
    Compute composite signal score. Positive = bullish, negative = bearish.

    Weights are informed by correlation research:
        Barrel rate      0.73 correlation with HR — highest weight
        Hard hit rate    0.39 correlation with HR
        Whiff rate       inverse: lower = better contact
        Chase rate       inverse: lower = better discipline

    Only scores metrics that have met their minimum sample size threshold.
    """
    score = 0.0

    barrel    = metrics.get("barrel_rate")
    hard_hit  = metrics.get("hard_hit_rate")
    whiff     = metrics.get("whiff_rate")
    chase     = metrics.get("chase_rate")
    xba       = metrics.get("xba")

    has_contact    = metrics.get("has_contact", False)
    has_discipline = metrics.get("has_discipline", False)
    has_expected   = metrics.get("has_expected", False)

    # ── Barrel rate — primary power signal (0.73 HR correlation) ──────────
    if has_contact and barrel is not None:
        diff = barrel - LEAGUE_AVG_BARREL
        if barrel >= 14:
            score += 3.0       # elite barrel rate
        elif barrel >= 10:
            score += 2.0       # above average
        elif diff > 0:
            score += 1.0       # above league avg
        elif barrel <= 3:
            score -= 2.5       # very poor barrel rate
        elif diff < -2:
            score -= 1.5       # below league avg

    # ── Hard hit rate — secondary power signal (0.39 HR correlation) ──────
    if has_contact and hard_hit is not None:
        diff = hard_hit - LEAGUE_AVG_HARDHIT
        if hard_hit >= 50:
            score += 1.5
        elif diff > 5:
            score += 1.0
        elif hard_hit <= 28:
            score -= 1.5
        elif diff < -5:
            score -= 1.0

    # ── Whiff rate — contact risk (lower is better) ────────────────────────
    if has_discipline and whiff is not None:
        diff = whiff - LEAGUE_AVG_WHIFF
        if whiff <= 15:
            score += 2.0       # elite contact
        elif whiff <= 18:
            score += 1.0
        elif whiff >= 33:
            score -= 2.5       # severe contact concerns
        elif whiff >= 28:
            score -= 1.5
        elif diff > 5:
            score -= 1.0

    # ── Chase rate — plate discipline (lower is better) ────────────────────
    if has_discipline and chase is not None:
        diff = chase - LEAGUE_AVG_CHASE
        if chase <= 22:
            score += 2.0       # elite discipline (Juan Soto territory)
        elif chase <= 25:
            score += 1.0
        elif chase >= 38:
            score -= 2.0       # chasing a lot
        elif chase >= 34:
            score -= 1.0
        elif diff > 5:
            score -= 0.5

    # ── xBA vs AVG gap — regression signal ────────────────────────────────
    # Only meaningful at 200+ PA — xBA should converge on true talent
    if has_expected and xba is not None:
        if xba >= 0.330:
            score += 1.5       # strong expected output
        elif xba >= 0.290:
            score += 0.5
        elif xba <= 0.230:
            score -= 1.5       # poor contact quality

    return score


def _classify_trend(score: float) -> str:
    if score >= SHOW_UP_THRESHOLD:
        return "up"
    if score <= SHOW_DOWN_THRESHOLD:
        return "down"
    return "neutral"


def _build_display(metrics: dict, score: float, trend: str) -> dict:
    """
    Build the display dict for the template.
    Shows sample size context so the reader can judge how much to trust it.
    Only includes metrics that have met their minimum sample size gate.
    """
    pa  = metrics.get("pa", 0)
    bbe = metrics.get("bbe", 0)

    has_contact    = metrics.get("has_contact", False)
    has_discipline = metrics.get("has_discipline", False)
    has_expected   = metrics.get("has_expected", False)

    # Sample size context label
    if pa >= MIN_PA_EXPECTED:
        data_label = f"{pa} PA — full signal"
    elif pa >= MIN_BBE_CONTACT * 3:  # rough proxy for 50 BBE
        data_label = f"{pa} PA / {bbe} BBE — contact metrics active"
    else:
        data_label = f"{pa} PA — discipline metrics only"

    return {
        "name":           metrics["name"],
        "trend":          trend,
        "signal_score":   round(score, 1),
        "data_label":     data_label,
        "pa":             pa,
        "bbe":            bbe,
        # Metrics — None if below threshold (template shows N/A)
        "whiff_rate":     metrics.get("whiff_rate")     if has_discipline else None,
        "chase_rate":     metrics.get("chase_rate")     if has_discipline else None,
        "barrel_rate":    metrics.get("barrel_rate")    if has_contact    else None,
        "hard_hit_rate":  metrics.get("hard_hit_rate")  if has_contact    else None,
        "xba":            metrics.get("xba")             if has_expected   else None,
        # Flags so template can show "—" vs "N/A" appropriately
        "has_discipline": has_discipline,
        "has_contact":    has_contact,
        "has_expected":   has_expected,
    }