"""
Pitcher start analysis — bullpen summary plus league pitcher usage.
"""
from src.data.yahoo_client import YahooClient


def build_bullpen_summary(bullpen: list[dict], cat_outcomes: list) -> str:
    """
    Returns a single sentence summarizing bullpen APP contribution.
    bullpen: list of dicts with keys name, expected_apps.
    cat_outcomes: CatOutcome objects from weekly_matchup_engine.
    """
    total_apps = sum(p.get("expected_apps", 0) for p in bullpen)
    app_outcome = next((c for c in cat_outcomes if c.cat == "APP"), None)

    if app_outcome is None:
        return f"Bullpen projects {total_apps:.1f} appearances this week."

    action   = app_outcome.action
    opp_avg  = app_outcome.opp_avg_val
    your_avg = app_outcome.your_avg_val

    if action == "SAFE":
        return (
            f"Bullpen projects {total_apps:.1f} apps — "
            f"sufficient to win APP (you {your_avg:.0f} vs opp avg {opp_avg:.0f})."
        )
    elif action == "HEDGE":
        return (
            f"Bullpen projects {total_apps:.1f} apps. "
            f"APP is close — opponent averages {opp_avg:.0f} vs your {your_avg:.0f}. "
            f"High-leverage RP usage this week matters."
        )
    else:  # NEED_HELP
        deficit = opp_avg - your_avg
        return (
            f"⚠️ Bullpen projects {total_apps:.1f} apps but APP is a losing category "
            f"(you avg {your_avg:.0f}, opp avg {opp_avg:.0f}, deficit {deficit:.0f}). "
            f"Consider streaming a high-appearance RP from FA."
        )


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


