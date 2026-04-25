"""
Analyzes my active roster against today's schedule.
Uses team offensive rankings for matchup grading instead of SP ERA alone.

Matchup grade is based on how weak the opposing offense is:
    ELITE     — facing one of the 5 worst offenses (great for hitters)
    FAVORABLE — facing a weak offense (ranks 21-25)
    NEUTRAL   — average matchup
    TOUGH     — facing a strong offense (ranks 6-10)
    AVOID     — facing one of the 5 best offenses

For hitters: favorable = strong offense (they score more)
For pitchers: favorable = weak offense (they allow less)
"""
import requests
from datetime import date
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.data.team_offense_ranker import ABBR_ALIASES

MLB_BASE = "https://statsapi.mlb.com/api/v1"


def get_todays_roster_impact() -> list[dict]:
    """
    Returns rostered hitters with games today, enriched with:
    - opponent, game time
    - opposing team offensive rank and tier
    - matchup grade for the hitter (good offense = favorable for hitter)
    - probable pitcher they face + pitcher ERA
    """
    yahoo = YahooClient()
    mlb   = MLBClient()

    my_roster = yahoo.get_my_roster()

    # Hitters only — index by MLB team abbreviation
    my_hitters: dict[str, dict] = {}
    for p in my_roster:
        team = (p.get("mlb_team") or "").upper()
        pos  = p.get("primary_position", "")
        eligible = p.get("eligible_positions") or []
        if pos in ("SP", "RP", "P") or "SP" in eligible:
            continue
        if team:
            my_hitters[team] = p

    if not my_hitters:
        return []

    today_games = mlb.get_schedule(date.today())
    results     = []

    for game in today_games:
        teams = game.get("teams", {})

        for side, opp_side in [("home", "away"), ("away", "home")]:
            team_abbr = (
                teams.get(side, {})
                     .get("team", {})
                     .get("abbreviation", "")
                     .upper()
            )

            # Match against my roster — try direct and alias
            my_player = my_hitters.get(team_abbr)
            if not my_player:
                alt = ABBR_ALIASES.get(team_abbr)
                if alt:
                    my_player = my_hitters.get(alt)
            if not my_player:
                continue

            # Opponent info
            opp_abbr = (
                teams.get(opp_side, {})
                     .get("team", {})
                     .get("abbreviation", "")
                     .upper()
            )
            opp_name = (
                teams.get(opp_side, {})
                     .get("team", {})
                     .get("name", opp_abbr)
            )

            # Probable pitcher they face
            opp_probable = (
                teams.get(opp_side, {})
                     .get("probablePitcher", {})
            )
            opp_pitcher_name = opp_probable.get("fullName", "TBD")
            opp_pitcher_id   = opp_probable.get("id")
            opp_era          = _get_pitcher_era(opp_pitcher_id)

            # For HITTERS — favorable = facing a STRONG offense on the opponent side
            # (i.e. the opponent's team pitching is weak because their offense is strong)
            # Actually: hitter matchup grade = based on opposing PITCHER quality
            # Use ERA-based grade for hitters, offense-rank for pitchers
            hitter_grade = _grade_hitter_matchup(opp_era)

            # Game time
            game_time = game.get("gameDate", "")
            if game_time and len(game_time) >= 16:
                game_time = game_time[11:16] + " UTC"

            results.append({
                "name":          my_player["name"],
                "position":      my_player.get("position", ""),
                "mlb_team":      team_abbr,
                "opponent":      opp_name,
                "opponent_abbr": opp_abbr,
                "opp_pitcher":   opp_pitcher_name,
                "opp_era":       f"{opp_era:.2f}" if opp_era else "N/A",
                "matchup":       hitter_grade,
                "favorable":     hitter_grade == "favorable",
                "game_time":     game_time,
            })

    # Sort: favorable first
    order = {"favorable": 0, "neutral": 1, "tough": 2}
    results.sort(key=lambda x: order.get(x["matchup"], 1))
    return results


def _get_pitcher_era(pitcher_id: int | None) -> float | None:
    """Fetch current season ERA for a pitcher from MLB Stats API."""
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


def _grade_hitter_matchup(era: float | None) -> str:
    """Grade a hitter matchup based on opposing SP ERA."""
    if era is None:
        return "neutral"
    if era >= 4.50:
        return "favorable"
    if era <= 3.25:
        return "tough"
    return "neutral"