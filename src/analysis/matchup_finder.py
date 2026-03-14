"""Streaming opportunity finder — pitcher vs. weak offense."""
from datetime import date, timedelta
import requests
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.config import FA_OWNERSHIP_THRESHOLD, STREAMING_WINDOW_DAYS, ROSTER_LAG_DAYS

MLB_BASE = "https://statsapi.mlb.com/api/v1"


def get_streaming_opportunities() -> list[dict]:
    """
    Finds unowned/low-owned SPs facing weak offenses within pickup window.
    Returns list ranked by composite score, soonest/best first.
    Only includes starts >= ROSTER_LAG_DAYS out.
    """
    yahoo = YahooClient()
    mlb = MLBClient()

    # Get free agent pitchers
    fa_pitchers = yahoo.get_free_agents(position="SP", limit=50)
    fa_pitchers = [
        p for p in fa_pitchers
        if p.get("ownership", 100.0) < FA_OWNERSHIP_THRESHOLD
    ]

    if not fa_pitchers:
        return []

    # Build name → FA player lookup
    fa_lookup = {
        p["name"].lower(): p
        for p in fa_pitchers
        if p.get("name")
    }

    # Get probable starters within window
    probable = mlb.get_probable_starters(days_ahead=STREAMING_WINDOW_DAYS)

    # Get team offense rankings
    offense_rankings = _get_team_offense_rankings()

    opportunities = []
    seen_pitchers = set()

    for starter in probable:
        days_out = starter.get("days_out", 0)

        # Skip starts too soon to act on (roster lag)
        if days_out < ROSTER_LAG_DAYS:
            continue

        name_lower = (starter.get("name") or "").lower()

        # Match against FA pitchers
        fa_player = fa_lookup.get(name_lower)
        if not fa_player:
            # Try last name match
            for fa_name, fa_p in fa_lookup.items():
                last = name_lower.split()[-1] if name_lower else ""
                if last and last in fa_name:
                    fa_player = fa_p
                    break

        if not fa_player or name_lower in seen_pitchers:
            continue
        seen_pitchers.add(name_lower)

        opponent = starter.get("opponent", "")
        opp_rank = offense_rankings.get(
            _normalize_team(opponent), 15
        )

        # Fetch pitcher ERA
        pitcher_id = starter.get("player_id")
        era = _get_pitcher_era(pitcher_id)

        if era and era > 5.50:
            continue  # skip bad pitchers even if matchup is good

        pitcher_dict = {
            "name": starter.get("name"),
            "era": era or 4.00,
            "ownership": fa_player.get("ownership", 0.0),
        }

        score = score_opportunity(pitcher_dict, opp_rank, days_out)

        # Deadline to add = game date minus roster lag
        game_date = date.fromisoformat(starter["game_date"])
        latest_add = game_date - timedelta(days=ROSTER_LAG_DAYS)
        days_until_deadline = (latest_add - date.today()).days

        opportunities.append({
            "name": starter.get("name"),
            "pitcher_team": starter.get("team", ""),
            "opponent": opponent,
            "game_date": starter["game_date"],
            "days_out": days_out,
            "era": f"{era:.2f}" if era else "N/A",
            "ownership": fa_player.get("ownership", 0.0),
            "opp_offense_rank": opp_rank,
            "score": score,
            "latest_add_date": latest_add.strftime("%A"),
            "urgent": days_until_deadline <= 0,
            "confirmed": starter.get("confirmed", False),
        })

    # Sort by score descending
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities[:6]  # top 6 streaming plays


def score_opportunity(pitcher: dict, opponent_rank: int, days_out: int) -> float:
    """
    Composite score. Higher = better streaming play.
    - pitcher_score: rewards low ERA
    - opponent_score: rewards weak offenses (high rank = worse offense)
    - timing_score: rewards starts further out (more time to add)
    """
    pitcher_score = max(0, (5.00 - pitcher.get("era", 4.50)) * 12)
    opponent_score = max(0, (opponent_rank - 10) * 4)
    timing_score = max(0, (STREAMING_WINDOW_DAYS - days_out + 1) * 2)
    return round(pitcher_score + opponent_score + timing_score, 2)


def _get_team_offense_rankings(days: int = 14) -> dict[str, int]:
    """
    Returns team abbreviation → offensive rank (1=best, 30=worst)
    based on runs scored over the last N days.
    Falls back to season totals if date-range data unavailable.
    """
    try:
        end = date.today()
        start = end - timedelta(days=days)
        url = (
            f"{MLB_BASE}/teams/stats"
            f"?stats=byDateRange&startDate={start}&endDate={end}"
            f"&group=hitting&sportId=1"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return _season_offense_rankings()

        teams_stats = resp.json().get("stats", [{}])[0].get("splits", [])
        if not teams_stats:
            return _season_offense_rankings()

        # Sort by runs scored descending
        sorted_teams = sorted(
            teams_stats,
            key=lambda x: int(x.get("stat", {}).get("runs", 0)),
            reverse=True
        )

        rankings = {}
        for rank, team_stat in enumerate(sorted_teams, start=1):
            abbr = (
                team_stat.get("team", {})
                          .get("abbreviation", "")
                          .upper()
            )
            if abbr:
                rankings[abbr] = rank

        return rankings

    except Exception:
        return _season_offense_rankings()


def _season_offense_rankings() -> dict[str, int]:
    """Fallback: season-total offensive rankings."""
    try:
        url = (
            f"{MLB_BASE}/teams/stats"
            f"?stats=season&group=hitting&sportId=1"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return {}

        teams_stats = resp.json().get("stats", [{}])[0].get("splits", [])
        sorted_teams = sorted(
            teams_stats,
            key=lambda x: int(x.get("stat", {}).get("runs", 0)),
            reverse=True,
        )
        return {
            team_stat.get("team", {}).get("abbreviation", "").upper(): rank
            for rank, team_stat in enumerate(sorted_teams, start=1)
            if team_stat.get("team", {}).get("abbreviation")
        }
    except Exception:
        return {}


def _get_pitcher_era(pitcher_id: int | None) -> float | None:
    """Fetch current season ERA for a pitcher."""
    if not pitcher_id:
        return None
    try:
        url = (
            f"{MLB_BASE}/people/{pitcher_id}"
            f"/stats?stats=season&group=pitching"
        )
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


def _normalize_team(name: str) -> str:
    """
    Convert full team name or partial name to MLB abbreviation.
    Used to match MLB schedule opponent names to offense rankings.
    """
    NAME_TO_ABBR = {
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
    name_lower = name.lower()
    for key, abbr in NAME_TO_ABBR.items():
        if key in name_lower:
            return abbr
    return name.upper()[:3]