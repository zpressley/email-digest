"""
Category standings analysis.
Compares current standings and matchup scoreboard from data/standings.json
which is updated daily by the trade bot pipeline.

Used in both daily digest (current matchup status) and
weekly review (full category breakdown).
"""
import json
import os
from datetime import date, timedelta
from src.data.snapshot_store import load_snapshot

MY_TEAM_ABBR   = os.getenv("MY_TEAM_ABBR", "WAR")
STANDINGS_FILE = "data/standings.json"
TOTAL_CATS     = 20  # FBP uses 20 scoring categories


def get_matchup_status() -> dict | None:
    """
    Returns current matchup status for WAR — used in daily digest.

    Reads from data/standings.json which is updated by the trade bot.

    Returns dict with:
        summary       — one sentence describing current matchup status
        record        — season record string e.g. "45-35-0"
        rank          — current league rank (int)
        cats_won      — categories winning this week
        cats_lost     — categories losing this week
        cats_tied     — categories tied this week
        opponent      — opponent abbreviation
        winning       — True if currently winning the matchup
    """
    current = _load_standings_file()
    if not current:
        return None

    my_standing = _find_my_team(current.get("standings", []))
    if not my_standing:
        return None

    record = my_standing.get("record", "N/A")
    rank   = my_standing.get("rank", "N/A")

    matchups   = current.get("matchups", [])
    my_matchup = _find_my_matchup(matchups)

    if my_matchup:
        cats_won, cats_lost, cats_tied, opp = _parse_matchup(my_matchup)
        winning = cats_won > cats_lost
        summary = _build_summary(cats_won, cats_lost, cats_tied, opp, record, rank)
        return {
            "summary":   summary,
            "record":    record,
            "rank":      rank,
            "cats_won":  cats_won,
            "cats_lost": cats_lost,
            "cats_tied": cats_tied,
            "opponent":  opp,
            "winning":   winning,
        }

    return {
        "summary": f"WAR sits at {record}, ranked #{rank} in the league.",
        "record":  record,
        "rank":    rank,
        "winning": None,
    }


def get_full_standings() -> list[dict]:
    """
    Returns full league standings — used in weekly digest.
    """
    current = _load_standings_file()
    if not current:
        return []
    return current.get("standings", [])


def get_weekly_summary() -> dict | None:
    """
    Returns a richer weekly summary for the Sunday digest.
    Compares current snapshot vs 7 days ago to show weekly movement.
    """
    current = _load_standings_file()
    if not current:
        return None

    my_standing = _find_my_team(current.get("standings", []))
    if not my_standing:
        return None

    today    = date.today()
    past_snap = load_snapshot(today - timedelta(days=7))

    rank_change = None
    if past_snap:
        past_standings = past_snap.get("daily", {}).get("standings", [])
        past_my_team   = _find_my_team(past_standings) if past_standings else None
        if past_my_team:
            rank_change = my_standing.get("rank", 0) - past_my_team.get("rank", 0)

    my_matchup = _find_my_matchup(current.get("matchups", []))
    cats_won = cats_lost = cats_tied = 0
    opponent = "opponent"
    if my_matchup:
        cats_won, cats_lost, cats_tied, opponent = _parse_matchup(my_matchup)

    rank   = my_standing.get("rank", "N/A")
    record = my_standing.get("record", "N/A")

    if rank_change is not None:
        if rank_change < 0:
            movement = f"up {abs(rank_change)} spot{'s' if abs(rank_change) > 1 else ''}"
        elif rank_change > 0:
            movement = f"down {rank_change} spot{'s' if rank_change > 1 else ''}"
        else:
            movement = "holding steady"
    else:
        movement = None

    return {
        "record":      record,
        "rank":        rank,
        "rank_change": rank_change,
        "movement":    movement,
        "cats_won":    cats_won,
        "cats_lost":   cats_lost,
        "cats_tied":   cats_tied,
        "opponent":    opponent,
        "winning":     cats_won > cats_lost,
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


def _parse_matchup(matchup_str: str) -> tuple[int, int, int, str]:
    """
    Parse matchup string into (cats_won, cats_lost, cats_tied, opponent).

    Handles formats:
        "WAR 12 vs HAM 8"   — 20 total cats, 0 tied
        "WAR 10 vs HAM 8"   — implies 2 tied if total < TOTAL_CATS
    """
    try:
        parts = matchup_str.split(" vs ")
        if len(parts) != 2:
            return 0, 0, 0, "opponent"

        left  = parts[0].strip().rsplit(" ", 1)
        right = parts[1].strip().rsplit(" ", 1)

        left_team  = left[0].strip()
        left_cats  = int(left[1]) if len(left) > 1 else 0
        right_team = right[0].strip()
        right_cats = int(right[1]) if len(right) > 1 else 0

        # Infer tied categories from total
        cats_tied = max(0, TOTAL_CATS - left_cats - right_cats)

        if left_team == MY_TEAM_ABBR:
            return left_cats, right_cats, cats_tied, right_team
        else:
            return right_cats, left_cats, cats_tied, left_team

    except Exception:
        return 0, 0, 0, "opponent"


def _build_summary(
    cats_won: int,
    cats_lost: int,
    cats_tied: int,
    opp: str,
    record: str,
    rank: int | str,
) -> str:
    total = cats_won + cats_lost + cats_tied
    if total == 0:
        return f"WAR sits at {record}, ranked #{rank}."

    tied_note = f" ({cats_tied} tied)" if cats_tied > 0 else ""

    if cats_won > cats_lost:
        margin = cats_won - cats_lost
        tone   = "dominant" if margin >= 8 else "strong" if margin >= 5 else "solid"
        return (
            f"WAR leads {opp} {cats_won}-{cats_lost}{tied_note} in categories "
            f"— a {tone} position heading into today. Season record: {record}, #{rank}."
        )
    elif cats_lost > cats_won:
        margin = cats_lost - cats_won
        tone   = "tough" if margin >= 8 else "rough" if margin >= 5 else "close"
        return (
            f"WAR trails {opp} {cats_won}-{cats_lost}{tied_note} in categories "
            f"— a {tone} spot heading into today. Season record: {record}, #{rank}."
        )
    else:
        return (
            f"WAR and {opp} are tied {cats_won}-{cats_lost}{tied_note} in categories "
            f"heading into today. Season record: {record}, #{rank}."
        )
