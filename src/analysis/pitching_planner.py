"""
Pitching planner — MLB Stats API only, no fantasy-host dependency.

Two outputs, both driven by probable starters on the MLB schedule crossed
with rolling team offense rankings:

    my_starts  — every probable start by one of my rostered pitchers over
                 the next PLANNER_DAYS days, graded by opponent offense.
    streamers  — unrostered (league-wide) probable starters facing weak
                 offenses inside the streaming window, scored and sorted.

Roster lag: a pickup made today joins the roster tomorrow, so streamer
starts today are excluded and starts tomorrow are flagged "add today".
"""
import requests
from datetime import date, timedelta

from src.config import ROSTER_LAG_DAYS, STREAMING_WINDOW_DAYS
from src.data import league_rosters, team_offense_ranker
from src.data.mlb_client import MLBClient

MLB_BASE = "https://statsapi.mlb.com/api/v1"

PLANNER_DAYS       = 7      # look-ahead for my rostered starters
STREAMER_MAX_ERA   = 5.00   # skip streamers with season ERA above this
STREAMER_MIN_RANK  = 16     # opponent offense must rank 16-30 (bottom half)
STREAMER_LIMIT     = 6


def get_pitching_planner() -> dict:
    """Returns {"my_starts": [...], "streamers": [...]}."""
    try:
        probables = _collect_probables(PLANNER_DAYS)
    except Exception as e:
        print(f"  ⚠️  Pitching planner: schedule fetch failed: {e}")
        return {"my_starts": [], "streamers": []}

    roster      = league_rosters.get_my_roster()
    my_ids      = {p["mlb_id"] for p in roster if p.get("mlb_id")}
    league_ids  = league_rosters.get_rostered_mlb_ids()

    my_starts = []
    streamer_pool = []
    for pr in probables:
        if pr["player_id"] in my_ids:
            my_starts.append(_build_my_start(pr))
        elif pr["player_id"] not in league_ids:
            streamer_pool.append(pr)

    my_starts.sort(key=lambda s: s["game_date"])
    streamers = _pick_streamers(streamer_pool)

    print(f"  ⚾ Planner: {len(my_starts)} of my starts, "
          f"{len(streamers)} streamer recs "
          f"(pool {len(streamer_pool)}, probables {len(probables)})")
    return {"my_starts": my_starts, "streamers": streamers}


# ── Probable starters with team abbreviations ────────────────────────────────

def _collect_probables(days_ahead: int) -> list[dict]:
    """
    Probable starters for the next N days, with MLB team ABBREVIATIONS for
    both sides (MLBClient.get_probable_starters only carries full names,
    which the offense ranker can't look up).
    """
    mlb = MLBClient()
    out = []
    today = date.today()
    for i in range(days_ahead):
        target = today + timedelta(days=i)
        for game in mlb.get_schedule(target):
            teams = game.get("teams", {})
            for side, opp_side in (("home", "away"), ("away", "home")):
                probable = teams.get(side, {}).get("probablePitcher") or {}
                if not probable.get("id"):
                    continue
                out.append({
                    "player_id": probable["id"],
                    "name":      probable.get("fullName", ""),
                    "team":      (teams.get(side, {}).get("team", {})
                                       .get("abbreviation", "")).upper(),
                    "opponent":  (teams.get(opp_side, {}).get("team", {})
                                       .get("abbreviation", "")).upper(),
                    "opponent_name": teams.get(opp_side, {}).get("team", {})
                                          .get("name", ""),
                    "game_date": target.isoformat(),
                    "days_out":  i,
                })
    return out


def _date_label(days_out: int, iso_date: str) -> str:
    if days_out == 0:
        return "Today"
    if days_out == 1:
        return "Tomorrow"
    try:
        return date.fromisoformat(iso_date).strftime("%a %b %-d")
    except ValueError:
        return iso_date


def _get_season_pitching(pitcher_id: int) -> dict:
    """Season ERA/WHIP/K9 for a pitcher; empty dict on any failure."""
    try:
        url  = f"{MLB_BASE}/people/{pitcher_id}/stats?stats=season&group=pitching"
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return {}
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        stat = splits[0].get("stat", {})
        return {
            "era":  _to_float(stat.get("era")),
            "whip": _to_float(stat.get("whip")),
            "k9":   _to_float(stat.get("strikeoutsPer9Inn")),
        }
    except Exception:
        return {}


def _to_float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── My rostered starts ───────────────────────────────────────────────────────

def _build_my_start(pr: dict) -> dict:
    grade = team_offense_ranker.get_matchup_grade(pr["opponent"])
    stats = _get_season_pitching(pr["player_id"])
    return {
        "name":        pr["name"],
        "team":        pr["team"],
        "opponent":    pr["opponent"],
        "game_date":   pr["game_date"],
        "date_label":  _date_label(pr["days_out"], pr["game_date"]),
        "opp_rank":    grade.get("rank", 15),
        "opp_tier":    grade.get("tier", "average"),
        "opp_grade":   grade.get("grade", "NEUTRAL"),
        "era":         stats.get("era"),
        "whip":        stats.get("whip"),
        "k9":          stats.get("k9"),
    }


# ── Streamers ────────────────────────────────────────────────────────────────

def _pick_streamers(pool: list[dict]) -> list[dict]:
    # Streaming window only, and never a start we can't roster in time.
    window = [
        p for p in pool
        if ROSTER_LAG_DAYS <= p["days_out"] <= STREAMING_WINDOW_DAYS
    ]

    # One start per pitcher — keep the soonest.
    seen: dict[int, dict] = {}
    for p in sorted(window, key=lambda x: x["days_out"]):
        seen.setdefault(p["player_id"], p)

    candidates = []
    for p in seen.values():
        grade = team_offense_ranker.get_matchup_grade(p["opponent"])
        rank  = grade.get("rank", 15)
        if rank < STREAMER_MIN_RANK:
            continue  # opponent offense too strong to stream against

        stats = _get_season_pitching(p["player_id"])
        era   = stats.get("era")
        if era is None or era > STREAMER_MAX_ERA:
            continue

        pitcher_score  = max(0.0, (STREAMER_MAX_ERA - era) * 2.0)
        opponent_score = (rank - 15) * 0.6          # worse offense → higher
        timing_score   = p["days_out"] * 0.4        # more lead time → higher

        latest_add = (date.fromisoformat(p["game_date"])
                      - timedelta(days=ROSTER_LAG_DAYS))
        candidates.append({
            "name":        p["name"],
            "team":        p["team"],
            "opponent":    p["opponent"],
            "game_date":   p["game_date"],
            "date_label":  _date_label(p["days_out"], p["game_date"]),
            "opp_rank":    rank,
            "opp_tier":    grade.get("tier", "average"),
            "era":         era,
            "whip":        stats.get("whip"),
            "k9":          stats.get("k9"),
            "score":       round(pitcher_score + opponent_score + timing_score, 1),
            "latest_add":  latest_add.strftime("%A"),
            "add_today":   latest_add <= date.today(),
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:STREAMER_LIMIT]
