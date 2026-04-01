"""
Category standings analysis.
Compares yesterday's standings snapshot vs today's to show daily movement.
Uses data/standings.json which is updated hourly by the trade bot pipeline.
"""
import json
import os
from datetime import date, timedelta
from src.data.snapshot_store import load_snapshot

MY_TEAM_ABBR = os.getenv("MY_TEAM_ABBR", "WAR")
STANDINGS_FILE = "data/standings.json"


def get_yesterday_results() -> dict | None:
    """
    Compares today's standings snapshot vs yesterday's to derive
    what happened in yesterday's matchup.

    Returns dict with:
        summary      — one sentence describing yesterday
        record       — current season record string
        rank         — current rank
        cats_won     — estimated categories won yesterday
        cats_lost    — estimated categories lost yesterday

    Returns None if insufficient snapshot data.
    """
    today     = date.today()
    yesterday = today - timedelta(days=1)

    today_snap     = load_snapshot(today)
    yesterday_snap = load_snapshot(yesterday)

    # Also load the live standings file from trade bot
    current_standings = _load_standings_file()

    if not current_standings:
        return None

    my_standing = _find_my_team(current_standings.get("standings", []))
    if not my_standing:
        return None

    # Try to find yesterday's matchup result in scoreboard
    matchups = current_standings.get("matchups", [])
    my_matchup = _find_my_matchup(matchups)

    record = my_standing.get("record", "N/A")
    rank   = my_standing.get("rank", "N/A")

    if my_matchup:
        cats_won, cats_lost, opp = _parse_matchup(my_matchup)
        summary = _build_summary(cats_won, cats_lost, opp, record, rank)
        return {
            "summary":   summary,
            "record":    record,
            "rank":      rank,
            "cats_won":  cats_won,
            "cats_lost": cats_lost,
        }

    # Fallback — just show standings position
    return {
        "summary": f"WAR sits at {record}, ranked {rank} in the league.",
        "record":  record,
        "rank":    rank,
    }


def _load_standings_file() -> dict | None:
    try:
        if not os.path.exists(STANDINGS_FILE):
            return None
        with open(STANDINGS_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"  ⚠️  Standings load error: {e}")
        return None


def _find_my_team(standings: list) -> dict | None:
    for s in standings:
        if s.get("team") == MY_TEAM_ABBR:
            return s
    return None


def _find_my_matchup(matchups: list) -> str | None:
    for m in matchups:
        if MY_TEAM_ABBR in m:
            return m
    return None


def _parse_matchup(matchup_str: str) -> tuple[int, int, str]:
    """
    Parse matchup string like 'WAR 12 vs HAM 8' into
    (cats_won, cats_lost, opponent_abbr).
    """
    try:
        parts = matchup_str.split(" vs ")
        if len(parts) != 2:
            return 0, 0, "opponent"

        left  = parts[0].strip().rsplit(" ", 1)
        right = parts[1].strip().rsplit(" ", 1)

        left_team  = left[0].strip()
        left_cats  = int(left[1]) if len(left) > 1 else 0
        right_team = right[0].strip()
        right_cats = int(right[1]) if len(right) > 1 else 0

        if left_team == MY_TEAM_ABBR:
            return left_cats, right_cats, right_team
        else:
            return right_cats, left_cats, left_team

    except Exception:
        return 0, 0, "opponent"


def _build_summary(cats_won: int, cats_lost: int, opp: str, record: str, rank: int) -> str:
    total = cats_won + cats_lost
    if total == 0:
        return f"WAR sits {record}, ranked {rank}."

    if cats_won > cats_lost:
        margin = cats_won - cats_lost
        tone   = "strong" if margin >= 5 else "solid"
        return (
            f"WAR won {cats_won}-{cats_lost} in categories vs {opp} yesterday — "
            f"a {tone} win. Season record sits at {record}, ranked {rank}."
        )
    elif cats_lost > cats_won:
        margin = cats_lost - cats_won
        tone   = "tough" if margin >= 5 else "close"
        return (
            f"WAR dropped a {tone} one to {opp} yesterday, {cats_won}-{cats_lost} in categories. "
            f"Season record sits at {record}, ranked {rank}."
        )
    else:
        return (
            f"WAR and {opp} split categories evenly {cats_won}-{cats_lost} yesterday — a tie. "
            f"Season record sits at {record}, ranked {rank}."
        )