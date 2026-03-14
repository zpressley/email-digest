"""Analyzes my active roster against today's schedule."""
from datetime import date
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient


# ERA thresholds for matchup grading
ERA_FAVORABLE = 4.50   # opponent SP ERA above this = favorable for hitter
ERA_TOUGH = 3.25       # opponent SP ERA below this = tough for hitter


def get_todays_roster_impact() -> list[dict]:
    """
    Returns rostered hitters with games today, enriched with:
    - opponent, game time
    - starting pitcher they face (name, ERA)
    - favorable / neutral / tough matchup flag
    """
    yahoo = YahooClient()
    mlb = MLBClient()

    my_roster = yahoo.get_my_roster()

    # Only hitters — pitchers don't need matchup grades
    my_hitters = {
        p["mlb_team"].upper(): p
        for p in my_roster
        if p.get("mlb_team")
        and p.get("primary_position") not in ("SP", "RP", "P")
        and "SP" not in (p.get("eligible_positions") or [])
    }

    if not my_hitters:
        return []

    today_games = mlb.get_schedule(date.today())
    results = []

    for game in today_games:
        teams = game.get("teams", {})

        for side, opp_side in [("home", "away"), ("away", "home")]:
            team_abbr = (
                teams.get(side, {})
                     .get("team", {})
                     .get("abbreviation", "")
                     .upper()
            )

            # Check if any of my hitters play for this team
            my_player = my_hitters.get(team_abbr)
            if not my_player:
                # Try partial match — Yahoo sometimes uses different abbrs
                my_player = _fuzzy_team_match(team_abbr, my_hitters)
            if not my_player:
                continue

            # Get opponent probable starter
            opp_probable = (
                teams.get(opp_side, {})
                     .get("probablePitcher", {})
            )
            opp_pitcher_name = opp_probable.get("fullName", "TBD")
            opp_pitcher_id = opp_probable.get("id")

            # Fetch ERA for opponent SP
            opp_era = _get_pitcher_era(mlb, opp_pitcher_id)

            # Grade the matchup
            matchup = _grade_matchup(opp_era)

            # Game time
            game_time = game.get("gameDate", "")
            if game_time:
                game_time = game_time[11:16] + " UTC"

            results.append({
                "name": my_player["name"],
                "position": my_player.get("position", ""),
                "mlb_team": team_abbr,
                "opponent_team": (
                    teams.get(opp_side, {})
                         .get("team", {})
                         .get("abbreviation", "")
                ),
                "opp_pitcher": opp_pitcher_name,
                "opp_era": f"{opp_era:.2f}" if opp_era else "N/A",
                "matchup": matchup,
                "favorable": matchup == "favorable",
                "game_time": game_time,
            })

    # Sort: favorable first, then neutral, then tough
    order = {"favorable": 0, "neutral": 1, "tough": 2}
    results.sort(key=lambda x: order.get(x["matchup"], 1))
    return results


def _get_pitcher_era(mlb: MLBClient, pitcher_id: int | None) -> float | None:
    """Fetch a pitcher's current season ERA from MLB Stats API."""
    if not pitcher_id:
        return None
    try:
        url = (
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}"
            f"/stats?stats=season&group=pitching"
        )
        import requests
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


def _grade_matchup(era: float | None) -> str:
    """Grade a matchup based on opponent SP ERA."""
    if era is None:
        return "neutral"
    if era >= ERA_FAVORABLE:
        return "favorable"
    if era <= ERA_TOUGH:
        return "tough"
    return "neutral"


def _fuzzy_team_match(abbr: str, hitters: dict) -> dict | None:
    """
    Handle cases where Yahoo and MLB use different team abbreviations.
    e.g. 'CWS' vs 'CHW', 'NYY' vs 'NY'
    """
    ABBR_MAP = {
        "CHW": "CWS", "CWS": "CHW",
        "TBR": "TB",  "TB":  "TBR",
        "SFG": "SF",  "SF":  "SFG",
        "SDP": "SD",  "SD":  "SDP",
        "KCR": "KC",  "KC":  "KCR",
        "WSN": "WSH", "WSH": "WSN",
        "ARI": "AZ",  "AZ":  "ARI",
    }
    alt = ABBR_MAP.get(abbr)
    return hitters.get(alt) if alt else None
