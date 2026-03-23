"""
Yahoo Fantasy API client.
Loads OAuth2 token from the shared token.json (fbp-trade-bot).
Refreshes and writes back to token.json so both repos stay in sync.

All Yahoo API responses use XML with a namespace prefix.
NS must be applied to every findall() call or results return empty.
"""
import json
import os
import time
import requests
from requests.auth import HTTPBasicAuth
from xml.etree import ElementTree as ET

from src.config import (
    YAHOO_TOKEN_PATH, YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET,
    YAHOO_LEAGUE_ID, YAHOO_GAME_KEY, YAHOO_TEAM_ID,
)

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
BASE_URL  = "https://fantasysports.yahooapis.com/fantasy/v2"
NS        = "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"

YAHOO_TEAM_MAP = {
    "1": "WIZ", "2": "B2J", "3": "CFL", "4": "HAM",
    "5": "JEP", "6": "LFB", "7": "LAW", "8": "SAD",
    "9": "DRO", "10": "RV", "11": "TBB", "12": "WAR"
}


class YahooClient:
    def __init__(self):
        self.client_id     = YAHOO_CLIENT_ID
        self.client_secret = YAHOO_CLIENT_SECRET
        self.league_id     = YAHOO_LEAGUE_ID
        self.game_key      = YAHOO_GAME_KEY
        self.team_id       = YAHOO_TEAM_ID
        self._token_data   = self._load_token()

    def _load_token(self) -> dict:
        if not os.path.exists(YAHOO_TOKEN_PATH):
            raise FileNotFoundError(f"token.json not found at: {YAHOO_TOKEN_PATH}")
        with open(YAHOO_TOKEN_PATH) as f:
            return json.load(f)

    def _save_token(self):
        with open(YAHOO_TOKEN_PATH, "w") as f:
            json.dump(self._token_data, f, indent=4)

    @property
    def league_key(self) -> str:
        return f"{self.game_key}.l.{self.league_id}"

    @property
    def team_key(self) -> str:
        return f"{self.game_key}.l.{self.league_id}.t.{self.team_id}"

    def authenticate(self):
        """Ensure we have a valid access token, refreshing if expired."""
        expires_at = self._token_data.get("expires_at", 0)
        if time.time() < expires_at - 60:
            return

        response = requests.post(
            TOKEN_URL,
            auth=HTTPBasicAuth(self.client_id, self.client_secret),
            data={
                "grant_type":    "refresh_token",
                "refresh_token": self._token_data["refresh_token"],
            },
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Yahoo auth failed ({response.status_code}): {response.text}"
            )
        new_data = response.json()
        new_data["expires_at"] = time.time() + new_data.get("expires_in", 3600)
        self._token_data.update(new_data)
        self._save_token()

    def _headers(self) -> dict:
        self.authenticate()
        return {
            "Authorization": f"Bearer {self._token_data['access_token']}",
            "Accept": "application/json",
        }

    def _get_xml(self, url: str) -> ET.Element:
        """GET a Yahoo API URL, parse and return the XML root element."""
        resp = requests.get(url, headers=self._headers(), timeout=15)
        if resp.status_code == 401:
            # Force refresh and retry once
            self._token_data["expires_at"] = 0
            resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return ET.fromstring(resp.text)

    # ── Public API methods ────────────────────────────────────────────────────

    def get_my_roster(self) -> list[dict]:
        """Returns all players on my active roster."""
        url  = f"{BASE_URL}/team/{self.team_key}/roster/players"
        root = self._get_xml(url)
        players = [_parse_player(p) for p in root.findall(f".//{{{NS}}}player")]
        print(f"  📋 Roster: {len(players)} players found")
        return players

    def get_league_standings(self) -> list[dict]:
        """Returns all teams with their current category stat totals."""
        url  = f"{BASE_URL}/league/{self.league_key}/standings"
        root = self._get_xml(url)
        teams = []
        for team in root.findall(f".//{{{NS}}}team"):
            team_data = {
                "team_id": _text(team, f"{{{NS}}}team_id"),
                "name":    _text(team, f".//{{{NS}}}name"),
                "stats":   {},
            }
            for stat in team.findall(f".//{{{NS}}}team_stats//{{{NS}}}stat"):
                stat_id = _text(stat, f"{{{NS}}}stat_id")
                value   = _text(stat, f"{{{NS}}}value")
                if stat_id:
                    team_data["stats"][stat_id] = value
            teams.append(team_data)
        return teams

    def get_free_agents(self, position: str = None, limit: int = 50) -> list[dict]:
        """Returns available free agents, optionally filtered by position."""
        pos_filter = f";position={position}" if position else ""
        url  = (
            f"{BASE_URL}/league/{self.league_key}/players"
            f";status=A{pos_filter};count={limit}"
        )
        root    = self._get_xml(url)
        players = []
        for p in root.findall(f".//{{{NS}}}player"):
            player = _parse_player(p)
            pct    = _text(p, f".//{{{NS}}}percent_owned/{{{NS}}}value")
            player["ownership"] = float(pct) if pct else 0.0
            players.append(player)
        return players

    def get_ownership_trends(self) -> list[dict]:
        """Returns players sorted by adds in the last 48 hours."""
        url  = f"{BASE_URL}/league/{self.league_key}/players;sort=AR;count=30"
        root = self._get_xml(url)
        players = []
        for p in root.findall(f".//{{{NS}}}player"):
            player = _parse_player(p)
            pct    = _text(p, f".//{{{NS}}}percent_owned/{{{NS}}}value")
            trend  = _text(p, f".//{{{NS}}}percent_owned/{{{NS}}}delta")
            player["ownership"] = float(pct) if pct else 0.0
            player["trend"] = (
                f"+{trend}%" if trend and float(trend) > 0
                else f"{trend}%" if trend
                else "—"
            )
            players.append(player)
        return players

    def get_all_team_rosters(self) -> dict[str, list]:
        """Returns every team's roster keyed by team_id string."""
        url  = f"{BASE_URL}/league/{self.league_key}/teams;out=roster/players"
        root = self._get_xml(url)
        teams = {}
        for team in root.findall(f".//{{{NS}}}team"):
            team_id = _text(team, f"{{{NS}}}team_id")
            players = [
                _parse_player(p)
                for p in team.findall(f".//{{{NS}}}player")
            ]
            teams[team_id] = players
        return teams


# ── Helpers ───────────────────────────────────────────────────────────────────

def _text(element: ET.Element, path: str) -> str | None:
    """Safe text extraction from an XML element."""
    node = element.find(path)
    return node.text if node is not None else None


def _parse_player(p: ET.Element) -> dict:
    """Extract a normalised player dict from a <player> XML element."""
    return {
        "yahoo_id":           _text(p, f"{{{NS}}}player_id"),
        "name":               _text(p, f".//{{{NS}}}full"),
        "first_name":         _text(p, f".//{{{NS}}}first") or "",
        "last_name":          _text(p, f".//{{{NS}}}last") or "",
        "position":           _text(p, f"{{{NS}}}display_position"),
        "primary_position":   _text(p, f".//{{{NS}}}primary_position"),
        "eligible_positions": [
            pos.text
            for pos in p.findall(
                f".//{{{NS}}}eligible_positions/{{{NS}}}position"
            )
            if pos.text
        ],
        "mlb_team":           _text(p, f"{{{NS}}}editorial_team_abbr"),
        "status":             _text(p, f"{{{NS}}}status"),
        "injury_note":        _text(p, f"{{{NS}}}injury_note"),
    }