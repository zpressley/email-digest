"""Minor league and recent call-up performance tracker.

Prospects are loaded from combined_players.json — the single source of truth.
MLB IDs come from the same file via UPID linkage.

Contract types in combined_players.json under 'contract_type':
    "Purchased Contract" → PC
    "Development Cont."  → DC
    "Blue Chip Contract" → BC
"""
import json
import os
import requests
from src.config import COMBINED_PLAYERS_PATH, MY_TEAM_ABBR

MLB_BASE = "https://statsapi.mlb.com/api/v1"

MINOR_LEAGUE_SPORT_IDS = {
    11: "AAA",
    12: "AA",
    13: "A+",
    14: "A",
}

CONTRACT_DISPLAY = {
    "Purchased Contract": "PC",
    "Development Cont.":  "DC",
    "Blue Chip Contract": "BC",
}

# Performance thresholds
BATTER_HOT_OPS   = 0.900
BATTER_COLD_AVG  = 0.210
PITCHER_HOT_ERA  = 2.50
PITCHER_COLD_ERA = 6.00
MIN_PA_BATTER    = 15
MIN_IP_PITCHER   = 5.0


def _format_contract(raw: str) -> str:
    """Normalize contract_type to short display label (PC / DC / BC)."""
    return CONTRACT_DISPLAY.get(raw, raw or "—")


def get_prospect_callouts() -> list[dict]:
    """
    Loads WAR prospects from combined_players.json (player_type == 'Farm'),
    fetches minor league stats from MLB Stats API using mlb_id,
    and returns notable callouts grouped as callup / positive / negative.
    """
    try:
        with open(COMBINED_PLAYERS_PATH) as f:
            all_players = json.load(f)
    except FileNotFoundError:
        print(f"⚠️  combined_players.json not found at {COMBINED_PLAYERS_PATH}")
        return []

    # My farm system only
    my_prospects = [
        p for p in all_players
        if p.get("player_type") == "Farm"
        and p.get("manager") == MY_TEAM_ABBR
        and p.get("upid")
    ]

    if not my_prospects:
        return []

    callouts = []

    for prospect in my_prospects:
        name     = prospect.get("name", "")
        mlb_id   = prospect.get("mlb_id")
        position = prospect.get("position", "")
        contract = _format_contract(prospect.get("contract_type", ""))

        if not mlb_id:
            # Player hasn't appeared in the MLB system yet — no stats available
            continue

        try:
            mlb_id = int(mlb_id)
        except (ValueError, TypeError):
            continue

        is_mlb_active = _check_mlb_active(mlb_id)
        minor_stats   = _get_minor_league_stats(mlb_id)

        if not minor_stats and not is_mlb_active:
            continue

        is_pitcher = position in ("SP", "RP", "P")

        callout = (
            _evaluate_pitcher(name, contract, minor_stats, is_mlb_active)
            if is_pitcher
            else _evaluate_batter(name, contract, minor_stats, is_mlb_active)
        )

        if callout:
            callouts.append(callout)

    # Sort: callup watch first, then positive, then negative
    order = {"callup": 0, "positive": 1, "negative": 2}
    callouts.sort(key=lambda x: order.get(x.get("type", "positive"), 1))
    return callouts


def _check_mlb_active(mlb_id: int) -> bool:
    """Returns True if the player is on an active MLB 26-man roster."""
    try:
        url  = f"{MLB_BASE}/people/{mlb_id}?hydrate=rosterEntries"
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return False
        return resp.json().get("people", [{}])[0].get("active", False)
    except Exception:
        return False


def _get_minor_league_stats(mlb_id: int) -> dict | None:
    """
    Returns the player's most recent minor league stat split.
    Tries AAA → AA → A+ → A in order.
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

        for group in resp.json().get("stats", []):
            splits = group.get("splits", [])
            if not splits:
                continue
            split    = splits[-1]
            sport_id = split.get("sport", {}).get("id")
            level    = MINOR_LEAGUE_SPORT_IDS.get(sport_id, "MiLB")
            return {
                "level": level,
                "stat":  split.get("stat", {}),
                "group": group.get("group", {}).get("displayName", ""),
            }
    except Exception:
        pass
    return None


def _evaluate_batter(
    name: str,
    contract: str,
    stats: dict | None,
    is_mlb_active: bool,
) -> dict | None:

    if is_mlb_active:
        return {
            "name":     name,
            "contract": contract,
            "level":    "MLB",
            "note":     "Recently called up to MLB roster 🔔",
            "positive": True,
            "type":     "callup",
        }

    if not stats:
        return None

    stat  = stats.get("stat", {})
    level = stats.get("level", "MiLB")

    try:
        avg = float(stat.get("avg", 0) or 0)
        ops = float(stat.get("ops", 0) or 0)
        pa  = int(stat.get("plateAppearances", 0) or 0)
        hr  = int(stat.get("homeRuns", 0) or 0)
        sb  = int(stat.get("stolenBases", 0) or 0)
    except (ValueError, TypeError):
        return None

    if pa < MIN_PA_BATTER:
        return None

    if ops >= BATTER_HOT_OPS:
        note = f".{int(avg * 1000):03d} AVG / {ops:.3f} OPS"
        if hr:
            note += f" / {hr} HR"
        if sb:
            note += f" / {sb} SB"
        note += f" ({pa} PA)"
        callout_type = "callup" if ops >= 1.000 else "positive"
        if callout_type == "callup":
            note += " · Call-up watch 🔔"
        return {
            "name":     name,
            "contract": contract,
            "level":    level,
            "note":     note,
            "positive": True,
            "type":     callout_type,
        }

    if avg <= BATTER_COLD_AVG and pa >= MIN_PA_BATTER * 2:
        return {
            "name":     name,
            "contract": contract,
            "level":    level,
            "note":     (
                f".{int(avg * 1000):03d} AVG / {ops:.3f} OPS "
                f"({pa} PA) · Slumping — monitor"
            ),
            "positive": False,
            "type":     "negative",
        }

    return None


def _evaluate_pitcher(
    name: str,
    contract: str,
    stats: dict | None,
    is_mlb_active: bool,
) -> dict | None:

    if is_mlb_active:
        return {
            "name":     name,
            "contract": contract,
            "level":    "MLB",
            "note":     "Recently called up to MLB roster 🔔",
            "positive": True,
            "type":     "callup",
        }

    if not stats:
        return None

    stat  = stats.get("stat", {})
    level = stats.get("level", "MiLB")

    try:
        era  = float(stat.get("era", 99) or 99)
        ip   = float(str(stat.get("inningsPitched", "0") or "0"))
        k    = int(stat.get("strikeOuts", 0) or 0)
        whip = float(stat.get("whip", 0) or 0)
    except (ValueError, TypeError):
        return None

    if ip < MIN_IP_PITCHER:
        return None

    if era <= PITCHER_HOT_ERA:
        note         = f"{era:.2f} ERA / {whip:.2f} WHIP / {k} K ({ip:.1f} IP)"
        callout_type = "callup" if era <= 1.50 else "positive"
        if callout_type == "callup":
            note += " · Call-up watch 🔔"
        return {
            "name":     name,
            "contract": contract,
            "level":    level,
            "note":     note,
            "positive": True,
            "type":     callout_type,
        }

    if era >= PITCHER_COLD_ERA:
        return {
            "name":     name,
            "contract": contract,
            "level":    level,
            "note":     (
                f"{era:.2f} ERA / {whip:.2f} WHIP "
                f"({ip:.1f} IP) · Struggling — monitor"
            ),
            "positive": False,
            "type":     "negative",
        }

    return None