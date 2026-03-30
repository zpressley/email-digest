"""
Pitcher start analysis — upcoming starts with sit/start recommendations.
Uses team offensive rankings to grade each matchup.
"""
from datetime import date, timedelta
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.data.team_offense_ranker import get_matchup_grade, ABBR_ALIASES
from src.config import ROSTER_LAG_DAYS


def get_my_upcoming_starts(days_ahead: int = 5) -> list[dict]:
    """
    Returns my rostered pitchers with confirmed starts in the next N days.
    Each start includes opponent offense rank and sit/start recommendation.
    """
    yahoo    = YahooClient()
    mlb      = MLBClient()

    my_roster = yahoo.get_my_roster()
    my_pitchers = {
        p["name"].lower(): p
        for p in my_roster
        if p.get("primary_position") in ("SP", "RP", "P")
        or "SP" in (p.get("eligible_positions") or [])
        or "RP"  in (p.get("eligible_positions") or [])
    }

    if not my_pitchers:
        return []

    probable  = mlb.get_probable_starters(days_ahead=days_ahead)
    starts    = []

    for starter in probable:
        name_lower = (starter.get("name") or "").lower()

        matched_player = None
        for my_name, my_player in my_pitchers.items():
            last_name = my_name.split()[-1] if my_name else ""
            if last_name and last_name in name_lower:
                matched_player = my_player
                break

        if not matched_player:
            continue

        days_out = starter["days_out"]
        act_now  = days_out <= ROSTER_LAG_DAYS

        # Get opponent abbreviation from start data
        opp_name = starter.get("opponent", "")
        opp_abbr = _name_to_abbr(opp_name)

        # Grade the matchup using offense rankings
        opp_grade      = get_matchup_grade(opp_abbr)
        opp_rank       = opp_grade.get("rank", 15)
        opp_tier       = opp_grade.get("tier", "average")
        opp_k_rate     = opp_grade.get("k_rate")
        opp_runs_pg    = opp_grade.get("runs_pg")

        # Sit/start recommendation
        if opp_rank >= 24:
            recommendation = "START"
            rec_color      = "green"
        elif opp_rank >= 18:
            recommendation = "LEAN START"
            rec_color      = "green"
        elif opp_rank >= 12:
            recommendation = "NEUTRAL"
            rec_color      = "yellow"
        elif opp_rank >= 6:
            recommendation = "LEAN SIT"
            rec_color      = "red"
        else:
            recommendation = "SIT"
            rec_color      = "red"

        starts.append({
            "name":              matched_player["name"],
            "mlb_team":          matched_player.get("mlb_team", ""),
            "opponent":          opp_name,
            "opponent_abbr":     opp_abbr,
            "game_date":         starter["game_date"],
            "days_out":          days_out,
            "confirmed":         starter["confirmed"],
            "act_now":           act_now,
            "position":          matched_player.get("position", "SP"),
            "opp_offense_rank":  opp_rank,
            "opp_offense_tier":  opp_tier,
            "opp_k_rate":        opp_k_rate,
            "opp_runs_pg":       opp_runs_pg,
            "recommendation":    recommendation,
            "rec_color":         rec_color,
        })

    starts.sort(key=lambda x: x["days_out"])
    return starts


def get_league_pitcher_usage() -> list[dict]:
    """
    Aggregates pitcher deployment across all 12 teams.
    Returns avg starts, avg RP appearances, workload notes per team.
    """
    yahoo = YahooClient()
    from src.data.yahoo_client import YAHOO_TEAM_MAP
    all_rosters = yahoo.get_all_team_rosters()
    usage = []

    for team_id, players in all_rosters.items():
        team_abbr = YAHOO_TEAM_MAP.get(str(team_id), f"Team {team_id}")

        starters  = [
            p for p in players
            if "SP" in (p.get("eligible_positions") or [])
        ]
        relievers = [
            p for p in players
            if "RP" in (p.get("eligible_positions") or [])
            and "SP" not in (p.get("eligible_positions") or [])
        ]

        note = ""
        if len(starters) >= 6:
            note = "🔥 Heavy SP load"
        elif len(relievers) >= 6:
            note = "⚠️ Bullpen heavy"
        elif len(starters) <= 3:
            note = "💤 Low SP usage"

        usage.append({
            "team_id":       team_id,
            "name":          team_abbr,
            "starter_count": len(starters),
            "reliever_count": len(relievers),
            "total_pitchers": len(starters) + len(relievers),
            "note":          note,
        })

    usage.sort(key=lambda x: x["starter_count"], reverse=True)
    return usage


def get_pitcher_last_start_date(player_id: int) -> date | None:
    """Returns date of the pitcher's most recent start."""
    mlb = MLBClient()
    try:
        stats  = mlb.get_player_recent_stats(player_id, days=10)
        splits = stats.get("stats", [{}])[0].get("splits", [])
        if splits:
            game_date_str = splits[-1].get("date")
            if game_date_str:
                return date.fromisoformat(game_date_str[:10])
    except Exception:
        pass
    return None


def _name_to_abbr(full_name: str) -> str:
    """Convert full team name to abbreviation for offense ranking lookup."""
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