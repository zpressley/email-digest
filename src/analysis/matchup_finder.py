"""
Streaming opportunity finder.
Uses team offensive rankings as the primary signal for matchup quality.

High K rate + low runs scored = ideal pitcher matchup.
Accounts for 1-day roster lag — only surfaces starts >= ROSTER_LAG_DAYS out.
"""
import requests
from datetime import date, timedelta
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.data.team_offense_ranker import (
    get_team_offense_rankings,
    get_matchup_grade,
    ABBR_ALIASES,
)
from src.config import FA_OWNERSHIP_THRESHOLD, STREAMING_WINDOW_DAYS, ROSTER_LAG_DAYS

MLB_BASE = "https://statsapi.mlb.com/api/v1"


def get_streaming_opportunities() -> list[dict]:
    """
    Finds unowned/low-owned SPs facing weak offenses within pickup window.
    Ranks by composite score weighted toward opponent offensive weakness.
    Only includes starts >= ROSTER_LAG_DAYS out.
    """
    yahoo = YahooClient()
    mlb   = MLBClient()

    fa_pitchers = yahoo.get_free_agents(position="SP", limit=50)
    fa_pitchers = [
        p for p in fa_pitchers
        if p.get("ownership", 100.0) < FA_OWNERSHIP_THRESHOLD
    ]

    if not fa_pitchers:
        return []

    fa_lookup = {
        p["name"].lower(): p
        for p in fa_pitchers
        if p.get("name")
    }

    probable    = mlb.get_probable_starters(days_ahead=STREAMING_WINDOW_DAYS)
    rankings    = get_team_offense_rankings(days=14)
    opportunities = []
    seen_pitchers = set()

    for starter in probable:
        days_out = starter.get("days_out", 0)
        if days_out < ROSTER_LAG_DAYS:
            continue

        name_lower = (starter.get("name") or "").lower()

        fa_player = fa_lookup.get(name_lower)
        if not fa_player:
            for fa_name, fa_p in fa_lookup.items():
                last = name_lower.split()[-1] if name_lower else ""
                if last and last in fa_name:
                    fa_player = fa_p
                    break

        if not fa_player or name_lower in seen_pitchers:
            continue
        seen_pitchers.add(name_lower)

        # Get opponent offense ranking
        opp_name  = starter.get("opponent", "")
        opp_abbr  = _name_to_abbr(opp_name)
        opp_grade = get_matchup_grade(opp_abbr)
        opp_rank  = opp_grade.get("rank", 15)
        opp_k_rate  = opp_grade.get("k_rate")
        opp_runs_pg = opp_grade.get("runs_pg")
        opp_tier  = opp_grade.get("tier", "average")

        # Fetch pitcher ERA
        pitcher_id = starter.get("player_id")
        era        = _get_pitcher_era(pitcher_id)

        # Skip clearly bad pitchers
        if era and era > 5.50:
            continue

        pitcher_dict = {
            "name":     starter.get("name"),
            "era":      era or 4.00,
            "ownership": fa_player.get("ownership", 0.0),
        }

        score = score_opportunity(pitcher_dict, opp_rank, days_out, opp_k_rate)

        # Deadline to add
        game_date   = date.fromisoformat(starter["game_date"])
        latest_add  = game_date - timedelta(days=ROSTER_LAG_DAYS)
        days_until  = (latest_add - date.today()).days

        opportunities.append({
            "name":              starter.get("name"),
            "pitcher_team":      starter.get("team", ""),
            "opponent":          opp_name,
            "opponent_abbr":     opp_abbr,
            "game_date":         starter["game_date"],
            "days_out":          days_out,
            "era":               f"{era:.2f}" if era else "N/A",
            "ownership":         fa_player.get("ownership", 0.0),
            "opp_offense_rank":  opp_rank,
            "opp_offense_tier":  opp_tier,
            "opp_k_rate":        opp_k_rate,
            "opp_runs_pg":       opp_runs_pg,
            "score":             score,
            "latest_add_date":   latest_add.strftime("%A"),
            "urgent":            days_until <= 0,
            "confirmed":         starter.get("confirmed", False),
        })

    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities[:6]


def score_opportunity(
    pitcher: dict,
    opponent_rank: int,
    days_out: int,
    opp_k_rate: float | None = None,
) -> float:
    """
    Composite score. Higher = better streaming play.

    Weights:
        opponent_offense (45%) — primary signal, offense rank 1-30
        pitcher_era (35%)      — quality filter
        k_rate_bonus (10%)     — extra credit for high-K opponents
        timing (10%)           — prefer starts further out
    """
    # Opponent weakness — rank 30 = worst offense = max score
    opp_score = max(0, (opponent_rank - 1) / 29 * 100) * 0.45

    # Pitcher ERA — lower ERA = higher score
    era_score = max(0, (5.50 - pitcher.get("era", 4.50)) / 5.50 * 100) * 0.35

    # K rate bonus — high opponent K rate = extra value
    k_bonus = 0.0
    if opp_k_rate and opp_k_rate > 24:
        k_bonus = min((opp_k_rate - 24) * 2, 20) * 0.10

    # Timing — prefer starts further out (more time to add)
    timing_score = max(0, (STREAMING_WINDOW_DAYS - days_out + 1) / STREAMING_WINDOW_DAYS * 100) * 0.10

    return round(opp_score + era_score + k_bonus + timing_score, 1)


def _get_pitcher_era(pitcher_id: int | None) -> float | None:
    if not pitcher_id:
        return None
    try:
        url  = f"{MLB_BASE}/people/{pitcher_id}/stats?stats=season&group=pitching"
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return None
        splits = (
            resp.json()
                .get("stats", [{}])[0]
                .get("splits", [{}])
        )
        if splits:
            era_str = splits[0].get("stat", {}).get("era", "")
            return float(era_str) if era_str else None
    except Exception:
        return None


def _name_to_abbr(full_name: str) -> str:
    NAME_MAP = {
        "yankees": "NYY", "red sox": "BOS", "blue jays": "TOR",
        "orioles": "BAL", "rays": "TB", "white sox": "CWS",
        "guardians": "CLE", "tigers": "DET", "royals": "KC",
        "twins": "MIN", "astros": "HOU", "angels": "LAA",
        "athletics": "OAK", "mariners": "SEA", "rangers": "TEX",
        "braves": "ATL", "marlins": "MIA", "mets": "NYM",
        "phillies": "PHI", "nationals": "WSH", "cubs": "CHC",
        "reds": "CIN", "brewers": "MIL", "pirates": "PIT",
        "cardinals": "STL", "diamondbacks": "ARI", "rockies": "COL",
        "dodgers": "LAD", "padres": "SD", "giants": "SF",
    }
    name_lower = full_name.lower()
    for key, abbr in NAME_MAP.items():
        if key in name_lower:
            return abbr
    return full_name.upper()[:3]