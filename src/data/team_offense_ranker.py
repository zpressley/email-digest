"""
Team offensive rankings using MLB Stats API rolling data.
Powers sit/start decisions, streaming finder, and weekly landscape.

Ranks all 30 teams by offensive weakness — the worse the offense,
the better for pitchers facing them.

Key metrics:
    k_rate      — strikeout rate (higher = better for pitchers)
    runs_pg     — runs scored per game (lower = better for pitchers)
    ops         — OPS against (lower = better for pitchers)
    composite   — weighted score combining all three

Rolling window defaults to 14 days. Falls back to season totals
if insufficient data (early season).
"""
import requests
from datetime import date, timedelta
from functools import lru_cache

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# MLB team ID → abbreviation mapping
TEAM_ID_TO_ABBR = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "STL",
    139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

# Yahoo/common abbreviation variants → canonical
ABBR_ALIASES = {
    "CHW": "CWS", "CWS": "CWS",
    "TBR": "TB",  "TB":  "TB",
    "SFG": "SF",  "SF":  "SF",
    "SDP": "SD",  "SD":  "SD",
    "KCR": "KC",  "KC":  "KC",
    "WSN": "WSH", "WSH": "WSH",
    "ARI": "ARI", "AZ":  "ARI",
    "LAA": "LAA", "ANA": "LAA",
    "NYY": "NYY", "NY":  "NYY",
}

# Tier labels based on composite rank
TIER_LABELS = {
    range(1, 6):   "elite",      # ranks 1-5: best offense, hardest to pitch against
    range(6, 11):  "strong",
    range(11, 21): "average",
    range(21, 26): "weak",
    range(26, 31): "terrible",   # ranks 26-30: worst offense, easiest to pitch against
}


@lru_cache(maxsize=1)
def get_team_offense_rankings(days: int = 14) -> dict[str, dict]:
    """
    Returns dict keyed by team abbreviation with offensive stats and ranks.

    Example:
        {
            "CWS": {
                "abbr": "CWS",
                "name": "Chicago White Sox",
                "rank": 28,
                "tier": "terrible",
                "k_rate": 27.4,
                "runs_pg": 3.1,
                "ops": 0.641,
                "composite_score": 82.1,
                "games": 12,
            },
            ...
        }

    Higher composite_score = worse offense = better pitcher matchup.
    """
    data = _fetch_rolling_stats(days)
    if not data:
        data = _fetch_season_stats()
    if not data:
        return {}

    # Compute composite score and rank
    ranked = _rank_teams(data)
    return ranked


def get_matchup_grade(team_abbr: str, days: int = 14) -> dict:
    """
    Returns matchup grade for a pitcher facing the given team.
    Normalizes abbreviation variants before lookup.
    """
    rankings = get_team_offense_rankings(days)
    normalized = ABBR_ALIASES.get(team_abbr.upper(), team_abbr.upper())

    if normalized not in rankings:
        return {
            "abbr": normalized,
            "rank": 15,
            "tier": "average",
            "grade": "NEUTRAL",
            "k_rate": None,
            "runs_pg": None,
        }

    team = rankings[normalized]
    rank  = team["rank"]

    if rank >= 26:
        grade = "ELITE"
    elif rank >= 21:
        grade = "FAVORABLE"
    elif rank >= 11:
        grade = "NEUTRAL"
    elif rank >= 6:
        grade = "TOUGH"
    else:
        grade = "AVOID"

    return {**team, "grade": grade}


def get_weakest_offenses(top_n: int = 10, days: int = 14) -> list[dict]:
    """
    Returns the N weakest offenses ranked worst-first.
    Used by streaming finder to identify best pitcher matchups.
    """
    rankings = get_team_offense_rankings(days)
    sorted_teams = sorted(
        rankings.values(),
        key=lambda x: x["rank"],
        reverse=True,  # highest rank number = worst offense = first
    )
    return sorted_teams[:top_n]


def get_offense_landscape() -> dict:
    """
    Returns full offensive landscape for weekly digest.
    Includes top 5 offenses, bottom 5 offenses, and notable trends.
    """
    rankings = get_team_offense_rankings(days=14)
    if not rankings:
        return {}

    sorted_by_rank = sorted(rankings.values(), key=lambda x: x["rank"])

    return {
        "best_offenses":   sorted_by_rank[:5],
        "worst_offenses":  sorted_by_rank[-5:][::-1],
        "all_teams":       sorted_by_rank,
        "updated_days":    14,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_rolling_stats(days: int) -> list[dict] | None:
    """Fetch rolling team batting stats over the last N days."""
    try:
        end   = date.today()
        start = end - timedelta(days=days)
        url   = (
            f"{MLB_BASE}/teams/stats"
            f"?stats=byDateRange"
            f"&startDate={start}&endDate={end}"
            f"&group=hitting&sportId=1"
        )
        resp = requests.get(url, timeout=12)
        if resp.status_code != 200:
            return None

        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        if len(splits) < 15:
            # Too few teams returned — insufficient data, use season
            return None

        return _parse_splits(splits)

    except Exception as e:
        print(f"  ⚠️  Team offense rolling fetch error: {e}")
        return None


def _fetch_season_stats() -> list[dict] | None:
    """Fallback: fetch season-total team batting stats."""
    try:
        url  = f"{MLB_BASE}/teams/stats?stats=season&group=hitting&sportId=1"
        resp = requests.get(url, timeout=12)
        if resp.status_code != 200:
            return None

        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        return _parse_splits(splits)

    except Exception as e:
        print(f"  ⚠️  Team offense season fetch error: {e}")
        return None


def _parse_splits(splits: list) -> list[dict]:
    """Parse MLB API team stat splits into normalized team dicts."""
    teams = []
    for split in splits:
        team_info = split.get("team", {})
        stat      = split.get("stat", {})
        team_id   = team_info.get("id")
        abbr      = TEAM_ID_TO_ABBR.get(team_id, team_info.get("abbreviation", "UNK"))

        try:
            games    = int(stat.get("gamesPlayed", 1) or 1)
            runs     = int(stat.get("runs", 0) or 0)
            so       = int(stat.get("strikeOuts", 0) or 0)
            pa       = int(stat.get("plateAppearances", 1) or 1)
            ops_str  = stat.get("ops", "0.000") or "0.000"
            ops      = float(ops_str)
            runs_pg  = round(runs / games, 2)
            k_rate   = round((so / pa) * 100, 1) if pa > 0 else 0.0
        except (ValueError, TypeError, ZeroDivisionError):
            continue

        teams.append({
            "abbr":    abbr,
            "name":    team_info.get("name", abbr),
            "team_id": team_id,
            "games":   games,
            "runs_pg": runs_pg,
            "k_rate":  k_rate,
            "ops":     ops,
        })

    return teams


def _rank_teams(teams: list[dict]) -> dict[str, dict]:
    """
    Compute composite offensive weakness score and assign ranks.
    Higher score = worse offense = better pitcher matchup.

    Composite = (k_rate_score * 0.35) + (runs_pg_score * 0.40) + (ops_score * 0.25)
    """
    if not teams:
        return {}

    # Normalize each metric to 0-100 scale
    max_k    = max(t["k_rate"]  for t in teams) or 1
    min_k    = min(t["k_rate"]  for t in teams)
    max_r    = max(t["runs_pg"] for t in teams) or 1
    min_r    = min(t["runs_pg"] for t in teams)
    max_ops  = max(t["ops"]     for t in teams) or 1
    min_ops  = min(t["ops"]     for t in teams)

    for team in teams:
        # K rate: higher k rate = worse offense = higher score
        k_score = _normalize(team["k_rate"], min_k, max_k, invert=False)
        # Runs per game: lower runs = worse offense = higher score
        r_score = _normalize(team["runs_pg"], min_r, max_r, invert=True)
        # OPS: lower OPS = worse offense = higher score
        o_score = _normalize(team["ops"], min_ops, max_ops, invert=True)

        team["composite_score"] = round(
            (k_score * 0.35) + (r_score * 0.40) + (o_score * 0.25), 1
        )

    # Sort by composite score descending — worst offense first
    sorted_teams = sorted(teams, key=lambda x: x["composite_score"], reverse=True)

    result = {}
    for rank, team in enumerate(sorted_teams, start=1):
        team["rank"] = rank
        team["tier"] = _get_tier(rank)
        result[team["abbr"]] = team

    return result


def _normalize(value: float, min_val: float, max_val: float, invert: bool) -> float:
    """Normalize a value to 0-100. Invert=True means lower value → higher score."""
    if max_val == min_val:
        return 50.0
    score = ((value - min_val) / (max_val - min_val)) * 100
    return round(100 - score if invert else score, 1)


def _get_tier(rank: int) -> str:
    for r, label in TIER_LABELS.items():
        if rank in r:
            return label
    return "average"