"""
League roster access — Yahoo-free.

Rosters come from combined_players.json (written daily by the trade bot
pipeline) instead of the Yahoo Fantasy API. The `manager` field in that
file holds the FBP team NAME (e.g. "Weekend Warriors"), while the rest of
this codebase works in abbreviations (e.g. "WAR"). data/managers.json
(copied from fbp-trade-bot by the workflow) provides the mapping; a
hardcoded fallback covers runs where the copy step failed.

Player dicts returned here are normalized to the shape the analyzers
expect (the same shape the old YahooClient.get_my_roster() produced):
    name, position, primary_position, eligible_positions,
    mlb_team, mlb_id, status
"""
import json
import os
from functools import lru_cache

from src.config import COMBINED_PLAYERS_PATH, MY_TEAM_ABBR

MANAGERS_JSON_PATH = os.getenv("MANAGERS_JSON_PATH", "data/managers.json")

# Known-good snapshot of managers.json (2026). sync'd copy takes precedence.
_FALLBACK_TEAM_NAMES = {
    "WIZ": "Whiz Kids",
    "B2J": "Btwn2Jackies",
    "CFL": "Country Fried Lamb",
    "HAM": "Hammers",
    "RV":  "Rick Vaughn",
    "LFB": "La Flama Blanca",
    "JEP": "Jepordizers!",
    "TBB": "The Bluke Blokes",
    "DRO": "Andromedans",
    "SAD": "not much of a donkey",
    "WAR": "Weekend Warriors",
    "DMN": "The Damn Yankees",
}

PITCHER_POSITIONS = {"SP", "RP", "P"}


@lru_cache(maxsize=1)
def load_team_names() -> dict[str, str]:
    """Returns abbr → team name, e.g. {"WAR": "Weekend Warriors", ...}."""
    try:
        if os.path.exists(MANAGERS_JSON_PATH):
            with open(MANAGERS_JSON_PATH) as f:
                data = json.load(f)
            teams = data.get("teams", {})
            out = {}
            for abbr, info in teams.items():
                if abbr.startswith("_") or not isinstance(info, dict):
                    continue
                name = info.get("name") or info.get("full_name")
                if name:
                    out[abbr] = name
            if out:
                return out
    except Exception as e:
        print(f"  ⚠️  managers.json load error: {e} — using fallback names")
    return dict(_FALLBACK_TEAM_NAMES)


def manager_matches(record: dict, abbr: str) -> bool:
    """
    True if a combined_players.json record belongs to the given FBP team.
    The `manager` field has held abbreviations in the past and full team
    names today — accept either.
    """
    manager = (record.get("manager") or "").strip()
    if not manager:
        return False
    if manager == abbr:
        return True
    return manager == load_team_names().get(abbr, "")


def _load_combined() -> list[dict]:
    try:
        with open(COMBINED_PLAYERS_PATH) as f:
            return json.load(f)
    except Exception as e:
        print(f"  ⚠️  combined_players.json load error: {e}")
        return []


def _normalize(record: dict) -> dict:
    positions = [p.strip() for p in (record.get("position") or "").split(",") if p.strip()]
    primary = positions[0] if positions else ""
    return {
        "name":               record.get("name", ""),
        "position":           record.get("position", ""),
        "primary_position":   primary,
        "eligible_positions": positions,
        "mlb_team":           (record.get("team") or "").upper(),
        "mlb_id":             record.get("mlb_id"),
        "status":             record.get("status", ""),
    }


def get_my_roster() -> list[dict]:
    """Active MLB roster for MY_TEAM_ABBR, normalized."""
    players = _load_combined()
    roster = [
        _normalize(p) for p in players
        if p.get("player_type") == "MLB" and manager_matches(p, MY_TEAM_ABBR)
    ]
    print(f"  📋 Roster ({MY_TEAM_ABBR}): {len(roster)} MLB players from combined_players.json")
    return roster


def get_rostered_names() -> set[str]:
    """Lowercased names of every MLB player rostered by ANY league team."""
    team_names = set(load_team_names().values()) | set(load_team_names().keys())
    return {
        (p.get("name") or "").lower()
        for p in _load_combined()
        if p.get("player_type") == "MLB"
        and (p.get("manager") or "").strip() in team_names
    }


def get_rostered_mlb_ids() -> set[int]:
    """MLB IDs of every MLB player rostered by ANY league team."""
    team_names = set(load_team_names().values()) | set(load_team_names().keys())
    return {
        p["mlb_id"]
        for p in _load_combined()
        if p.get("player_type") == "MLB"
        and p.get("mlb_id")
        and (p.get("manager") or "").strip() in team_names
    }


def is_pitcher(player: dict) -> bool:
    eligible = player.get("eligible_positions") or []
    return bool(PITCHER_POSITIONS & set(eligible))
