"""Minor league and recent call-up performance tracker."""
from datetime import date, timedelta
import requests
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# Minor league sport IDs
MINOR_LEAGUE_SPORT_IDS = {
    11: "AAA",
    12: "AA",
    13: "A+",
    14: "A",
}

# Thresholds for flagging strong/poor performance
BATTER_HOT_OPS = 0.900
BATTER_COLD_AVG = 0.210
PITCHER_HOT_ERA = 2.50
PITCHER_COLD_ERA = 6.00
MIN_PA_BATTER = 15
MIN_IP_PITCHER = 5.0


def get_prospect_callouts() -> list[dict]:
    """
    For farm-system players and recent call-ups:
    - flags strong performance (green)
    - flags poor performance (red)
    - flags call-up watch candidates
    Returns empty list if nothing notable.
    """
    yahoo = YahooClient()
    mlb = MLBClient()

    my_roster = yahoo.get_my_roster()

    # Identify farm / prospect players — those on NA slots or
    # with no active MLB team assignment are likely minor leaguers
    farm_players = [
        p for p in my_roster
        if _is_prospect(p)
    ]

    if not farm_players:
        return []

    callouts = []

    for player in farm_players:
        name = player.get("name", "")
        yahoo_id = player.get("yahoo_id")
        first = player.get("first_name", "")
        last = player.get("last_name", "")

        # Try to get MLB ID from Yahoo player data
        mlb_id = _get_mlb_id_from_yahoo(yahoo_id)
        if not mlb_id:
            continue

        # Check if recently called up to MLB
        is_active_mlb = _check_mlb_active(mlb_id)

        # Get minor league stats
        minor_stats = _get_minor_league_stats(mlb_id)

        if not minor_stats and not is_active_mlb:
            continue

        position = player.get("primary_position", "")
        is_pitcher = position in ("SP", "RP", "P")

        if is_pitcher:
            callout = _evaluate_pitcher(name, minor_stats, is_active_mlb)
        else:
            callout = _evaluate_batter(name, minor_stats, is_active_mlb)

        if callout:
            callouts.append(callout)

    # Sort: positive callouts first, then call-up watch, then negatives
    order = {"positive": 0, "callup": 1, "negative": 2}
    callouts.sort(key=lambda x: order.get(x.get("type", "positive"), 1))

    return callouts


def _is_prospect(player: dict) -> bool:
    """
    Heuristic: a player is a prospect if they have no active MLB status
    or are on a minor league assignment.
    """
    status = (player.get("status") or "").upper()
    injury = player.get("injury_note") or ""
    # Active MLB players have no status flag or are active
    if status in ("", "ACTIVE", "ACT"):
        return False
    # NA = not active = minor leaguer or injured list
    if status in ("NA", "DL", "IL", "DTD"):
        return True
    return False


def _get_mlb_id_from_yahoo(yahoo_id: str | None) -> int | None:
    """
    Look up MLB player ID from Yahoo player ID via MLB Stats API suggest.
    """
    if not yahoo_id:
        return None
    try:
        # Use MLB Stats API player search — not perfect but works for known players
        url = f"https://statsapi.mlb.com/api/v1/people/{yahoo_id}"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            people = resp.json().get("people", [])
            if people:
                return people[0].get("id")
    except Exception:
        pass
    return None


def _check_mlb_active(mlb_id: int) -> bool:
    """Returns True if the player is on an active MLB 26-man roster."""
    try:
        url = f"{MLB_BASE}/people/{mlb_id}?hydrate=currentTeam,rosterEntries"
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return False
        person = resp.json().get("people", [{}])[0]
        roster_status = person.get("rosterStatus", "")
        return roster_status == "Active"
    except Exception:
        return False


def _get_minor_league_stats(mlb_id: int) -> dict | None:
    """
    Returns the player's most recent minor league stats split.
    Tries AAA first, then AA, A+, A.
    """
    try:
        sport_ids = ",".join(str(s) for s in MINOR_LEAGUE_SPORT_IDS.keys())
        url = (
            f"{MLB_BASE}/people/{mlb_id}/stats"
            f"?stats=season&group=hitting,pitching"
            f"&sportId={sport_ids}"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None

        stats_groups = resp.json().get("stats", [])
        for group in stats_groups:
            splits = group.get("splits", [])
            if splits:
                # Return the most recent / highest level split
                split = splits[-1]
                sport_id = split.get("sport", {}).get("id")
                level = MINOR_LEAGUE_SPORT_IDS.get(sport_id, "MiLB")
                return {
                    "level": level,
                    "stat": split.get("stat", {}),
                    "group": group.get("group", {}).get("displayName", ""),
                }
    except Exception:
        pass
    return None


def _evaluate_batter(name: str, stats: dict | None, is_mlb_active: bool) -> dict | None:
    """Evaluate a minor league batter and return a callout if notable."""
    if is_mlb_active:
        return {
            "name": name,
            "level": "MLB",
            "note": "Recently called up to MLB roster 🔔",
            "positive": True,
            "type": "callup",
        }

    if not stats or stats.get("group") not in ("hitting", None):
        return None

    stat = stats.get("stat", {})
    level = stats.get("level", "MiLB")

    try:
        avg = float(stat.get("avg", 0) or 0)
        ops = float(stat.get("ops", 0) or 0)
        pa = int(stat.get("plateAppearances", 0) or 0)
        hr = int(stat.get("homeRuns", 0) or 0)
        sb = int(stat.get("stolenBases", 0) or 0)
    except (ValueError, TypeError):
        return None

    if pa < MIN_PA_BATTER:
        return None

    if ops >= BATTER_HOT_OPS:
        note = f".{int(avg*1000)} AVG / {ops:.3f} OPS"
        if hr > 0:
            note += f" / {hr} HR"
        if sb > 0:
            note += f" / {sb} SB"
        note += f" ({pa} PA)"

        # Call-up watch if OPS is elite
        if ops >= 1.000:
            note += " · Call-up watch 🔔"
            callout_type = "callup"
        else:
            callout_type = "positive"

        return {
            "name": name,
            "level": level,
            "note": note,
            "positive": True,
            "type": callout_type,
        }

    if avg <= BATTER_COLD_AVG and pa >= MIN_PA_BATTER * 2:
        return {
            "name": name,
            "level": level,
            "note": f".{int(avg*1000)} AVG / {ops:.3f} OPS ({pa} PA) · Slumping — monitor",
            "positive": False,
            "type": "negative",
        }

    return None


def _evaluate_pitcher(name: str, stats: dict | None, is_mlb_active: bool) -> dict | None:
    """Evaluate a minor league pitcher and return a callout if notable."""
    if is_mlb_active:
        return {
            "name": name,
            "level": "MLB",
            "note": "Recently called up to MLB roster 🔔",
            "positive": True,
            "type": "callup",
        }

    if not stats:
        return None

    stat = stats.get("stat", {})
    level = stats.get("level", "MiLB")

    try:
        era = float(stat.get("era", 99) or 99)
        ip_str = str(stat.get("inningsPitched", "0") or "0")
        ip = float(ip_str)
        k = int(stat.get("strikeOuts", 0) or 0)
        whip = float(stat.get("whip", 0) or 0)
    except (ValueError, TypeError):
        return None

    if ip < MIN_IP_PITCHER:
        return None

    if era <= PITCHER_HOT_ERA:
        note = f"{era:.2f} ERA / {whip:.2f} WHIP / {k} K ({ip:.1f} IP)"
        if era <= 1.50:
            note += " · Call-up watch 🔔"
            callout_type = "callup"
        else:
            callout_type = "positive"

        return {
            "name": name,
            "level": level,
            "note": note,
            "positive": True,
            "type": callout_type,
        }

    if era >= PITCHER_COLD_ERA:
        return {
            "name": name,
            "level": level,
            "note": f"{era:.2f} ERA / {whip:.2f} WHIP ({ip:.1f} IP) · Struggling — monitor",
            "positive": False,
            "type": "negative",
        }

    return None