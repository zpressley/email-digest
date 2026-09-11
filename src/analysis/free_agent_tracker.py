"""
Free agent heat index — Yahoo-free.

Old design ranked free agents by Yahoo 48-hour add trends. Without the
Yahoo API, "heat" is now measured directly: how has every unrostered
hitter actually performed over the last FA_HOT_DAYS days?

Primary source: MLB Stats API byDateRange leaders (one call).
Fallback:       pybaseball batting_stats_range (Baseball-Reference).

Unrostered = not on any FBP team per combined_players.json.
"""
import requests
from datetime import date, timedelta

from src.data import league_rosters

MLB_BASE = "https://statsapi.mlb.com/api/v1"

FA_HOT_DAYS  = 7     # rolling performance window
FA_MIN_PA    = 12    # minimum PA inside the window
FA_LIMIT     = 8     # how many hot hitters to surface
LEADER_LIMIT = 150   # how deep to pull the leaderboard before filtering


def get_hot_free_agents() -> list[dict]:
    """
    Returns the hottest unrostered hitters over the last FA_HOT_DAYS days:
    name, position, team, and a compact stat line for the window.
    """
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=FA_HOT_DAYS - 1)

    rows = _fetch_mlb_leaders(start, end)
    if not rows:
        rows = _fetch_pybaseball_leaders(start, end)
    if not rows:
        print("  ⚠️  FA heat: no leaderboard data from MLB API or pybaseball")
        return []

    rostered_ids   = league_rosters.get_rostered_mlb_ids()
    rostered_names = league_rosters.get_rostered_names()

    hot = []
    for row in rows:
        if row["pa"] < FA_MIN_PA:
            continue
        if row.get("player_id") and row["player_id"] in rostered_ids:
            continue
        if row["name"].lower() in rostered_names:
            continue

        hot.append({
            "name":      row["name"],
            "position":  row.get("position", ""),
            "team":      row.get("team", ""),
            "stat_line": (
                f"{row['avg']} AVG · {row['ops']} OPS · {row['hr']} HR · "
                f"{row['rbi']} RBI · {row['sb']} SB in {row['pa']} PA"
            ),
            "window":    f"last {FA_HOT_DAYS} days",
            "ops_val":   _safe_float(row["ops"]),
        })
        if len(hot) >= FA_LIMIT:
            break

    print(f"  🔥 FA heat: {len(hot)} unrostered hitters surfaced "
          f"from {len(rows)} leaderboard rows")
    return hot


# ── Primary: MLB Stats API ───────────────────────────────────────────────────

def _fetch_mlb_leaders(start: date, end: date) -> list[dict]:
    """Top hitters by OPS over the window via the stats endpoint."""
    url = (
        f"{MLB_BASE}/stats"
        f"?stats=byDateRange&group=hitting&sportIds=1"
        f"&startDate={start}&endDate={end}"
        f"&sortStat=onBasePlusSlugging&order=desc"
        f"&limit={LEADER_LIMIT}"
    )
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            print(f"  ⚠️  FA heat: MLB leaders HTTP {resp.status_code}")
            return []
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
    except Exception as e:
        print(f"  ⚠️  FA heat: MLB leaders fetch failed: {e}")
        return []

    rows = []
    for s in splits:
        player = s.get("player", {})
        stat   = s.get("stat", {})
        if not player.get("id"):
            continue
        rows.append({
            "player_id": player["id"],
            "name":      player.get("fullName", ""),
            "position":  s.get("position", {}).get("abbreviation", ""),
            "team":      s.get("team", {}).get("abbreviation", ""),
            "pa":        int(stat.get("plateAppearances", 0) or 0),
            "avg":       stat.get("avg", ".000"),
            "ops":       stat.get("ops", ".000"),
            "hr":        int(stat.get("homeRuns", 0) or 0),
            "rbi":       int(stat.get("rbi", 0) or 0),
            "sb":        int(stat.get("stolenBases", 0) or 0),
        })
    return rows


# ── Fallback: pybaseball / Baseball-Reference ────────────────────────────────

def _fetch_pybaseball_leaders(start: date, end: date) -> list[dict]:
    try:
        from pybaseball import batting_stats_range
        df = batting_stats_range(start.isoformat(), end.isoformat())
    except Exception as e:
        print(f"  ⚠️  FA heat: pybaseball fallback failed: {e}")
        return []
    if df is None or df.empty:
        return []

    rows = []
    for _, r in df.sort_values("OPS", ascending=False).head(LEADER_LIMIT).iterrows():
        try:
            rows.append({
                "player_id": int(r["mlbID"]) if "mlbID" in df.columns else None,
                "name":      str(r.get("Name", "")),
                "position":  "",
                "team":      str(r.get("Tm", "")),
                "pa":        int(r.get("PA", 0) or 0),
                "avg":       f"{float(r.get('BA', 0) or 0):.3f}".lstrip("0"),
                "ops":       f"{float(r.get('OPS', 0) or 0):.3f}".lstrip("0"),
                "hr":        int(r.get("HR", 0) or 0),
                "rbi":       int(r.get("RBI", 0) or 0),
                "sb":        int(r.get("SB", 0) or 0),
            })
        except Exception:
            continue
    return rows


def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
