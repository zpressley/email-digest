"""Pitcher start analysis — upcoming starts and probable starter logic."""
from datetime import date, timedelta
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.config import ROSTER_LAG_DAYS


def get_my_upcoming_starts(days_ahead: int = 5) -> list[dict]:
    """
    Returns my rostered pitchers with confirmed or probable starts
    in the next N days. Flags starts within ROSTER_LAG_DAYS as 'act now'.
    """
    yahoo = YahooClient()
    mlb = MLBClient()

    # Get my rostered pitchers
    my_roster = yahoo.get_my_roster()
    my_pitchers = {
        p["name"].lower(): p
        for p in my_roster
        if p.get("primary_position") in ("SP", "RP", "P")
        or "SP" in (p.get("eligible_positions") or [])
        or "RP" in (p.get("eligible_positions") or [])
    }

    if not my_pitchers:
        return []

    # Get all probable starters across the window
    probable = mlb.get_probable_starters(days_ahead=days_ahead)

    starts = []
    for starter in probable:
        name_lower = (starter.get("name") or "").lower()

        # Fuzzy match against my roster — check if any of my pitcher names
        # appear in the probable starter name or vice versa
        matched_player = None
        for my_name, my_player in my_pitchers.items():
            last_name = my_name.split()[-1] if my_name else ""
            if last_name and last_name in name_lower:
                matched_player = my_player
                break

        if not matched_player:
            continue

        days_out = starter["days_out"]
        act_now = days_out <= ROSTER_LAG_DAYS

        starts.append({
            "name": matched_player["name"],
            "mlb_team": matched_player.get("mlb_team", ""),
            "opponent": starter["opponent"],
            "game_date": starter["game_date"],
            "days_out": days_out,
            "confirmed": starter["confirmed"],
            "act_now": act_now,
            "position": matched_player.get("position", "SP"),
        })

    # Sort by soonest first
    starts.sort(key=lambda x: x["days_out"])
    return starts


def get_league_pitcher_usage() -> list[dict]:
    """
    Aggregates pitcher deployment across all 12 teams.
    Returns avg starts, avg RP appearances, workload notes per team.
    """
    yahoo = YahooClient()
    mlb = MLBClient()

    all_rosters = yahoo.get_all_team_rosters()
    # Map Yahoo team IDs to abbreviations
    from src.data.yahoo_client import YAHOO_TEAM_MAP
    usage = []

    for team_id, players in all_rosters.items():
        team_abbr = YAHOO_TEAM_MAP.get(str(team_id), f"Team {team_id}")

        starters = [
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
            "team_id": team_id,
            "name": team_abbr,
            "starter_count": len(starters),
            "reliever_count": len(relievers),
            "total_pitchers": len(starters) + len(relievers),
            "note": note,
        })

    usage.sort(key=lambda x: x["starter_count"], reverse=True)
    return usage


def get_pitcher_last_start_date(player_id: int) -> date | None:
    """Returns date of the pitcher's most recent start from MLB API."""
    mlb = MLBClient()
    try:
        stats = mlb.get_player_recent_stats(player_id, days=10)
        splits = (
            stats.get("stats", [{}])[0]
                 .get("splits", [])
        )
        if splits:
            game_date_str = splits[-1].get("date")
            if game_date_str:
                return date.fromisoformat(game_date_str[:10])
    except Exception:
        pass
    return None