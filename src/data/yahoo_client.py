"""
Yahoo Fantasy API client.
Loads OAuth2 token from the shared token.json (fbp-trade-bot).
Refreshes and writes back to token.json so both repos stay in sync.
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
BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"


class YahooClient:
    def __init__(self):
        self.client_id = YAHOO_CLIENT_ID
        self.client_secret = YAHOO_CLIENT_SECRET
        self.league_id = YAHOO_LEAGUE_ID
        self.game_key = YAHOO_GAME_KEY
        self.team_id = YAHOO_TEAM_ID
        self._token_data = self._load_token()

    def _load_token(self) -> dict:
        """Load OAuth token from token.json (shared with trade bot)."""
        if not os.path.exists(YAHOO_TOKEN_PATH):
            raise FileNotFoundError(f"token.json not found at: {YAHOO_TOKEN_PATH}")
        with open(YAHOO_TOKEN_PATH) as f:
            return json.load(f)

    def _save_token(self):
        """Write updated token back to token.json so both repos stay in sync."""
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
            return  # still valid

        response = requests.post(
            TOKEN_URL,
            auth=HTTPBasicAuth(self.client_id, self.client_secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._token_data["refresh_token"],
            },
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Yahoo auth failed ({response.status_code}): {response.text}")

        new_data = response.json()
        new_data["expires_at"] = time.time() + new_data.get("expires_in", 3600)
        self._token_data.update(new_data)
        self._save_token()  # keep trade bot in sync

    def _headers(self) -> dict:
        self.authenticate()
        return {"Authorization": f"Bearer {self._token_data['access_token']}", "Accept": "application/json"}

    def _get_xml(self, url: str) -> ET.Element:
        """GET a Yahoo API URL and parse the XML response."""
        resp = requests.get(url, headers=self._headers(), timeout=15)
        if resp.status_code == 401:
            # Force a token refresh and retry once
            self._token_data["expires_at"] = 0
            resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return ET.fromstring(resp.text)

    # ── Public API methods ───────────────────────────────────────────────────

    def get_my_roster(self) -> list[dict]:
        """Returns list of player dicts on my active roster."""
        url = f"{BASE_URL}/team/{self.team_key}/roster/players"
        root = self._get_xml(url)
        return [_parse_player(p) for p in root.findall(".//player")]

    def get_league_standings(self) -> list[dict]:
        """Returns all teams with their current category stat totals."""
        url = f"{BASE_URL}/league/{self.league_key}/standings"
        root = self._get_xml(url)
        teams = []
        for team in root.findall(".//team"):
            team_data = {
                "team_id": _text(team, "team_id"),
                "name": _text(team, ".//name"),
                "stats": {},
            }
            for stat in team.findall(".//team_stats//stat"):
                stat_id = _text(stat, "stat_id")
                value = _text(stat, "value")
                if stat_id:
                    team_data["stats"][stat_id] = value
            teams.append(team_data)
        return teams

    def get_free_agents(self, position: str = None, limit: int = 50) -> list[dict]:
        """Returns available free agents, optionally filtered by position."""
        pos_filter = f";position={position}" if position else ""
        url = (
            f"{BASE_URL}/league/{self.league_key}/players"
            f";status=A{pos_filter};count={limit}"
        )
        root = self._get_xml(url)
        players = []
        for p in root.findall(".//player"):
            player = _parse_player(p)
            pct_owned = _text(p, ".//percent_owned/value")
            player["ownership"] = float(pct_owned) if pct_owned else 0.0
            players.append(player)
        return players

    def get_ownership_trends(self) -> list[dict]:
        """Returns players sorted by adds in the last 48 hours (rising ownership)."""
        url = f"{BASE_URL}/league/{self.league_key}/players;sort=AR;count=30"
        root = self._get_xml(url)
        players = []
        for p in root.findall(".//player"):
            player = _parse_player(p)
            pct_owned = _text(p, ".//percent_owned/value")
            trend = _text(p, ".//percent_owned/delta")
            player["ownership"] = float(pct_owned) if pct_owned else 0.0
            if trend:
                player["trend"] = f"+{trend}%" if float(trend) > 0 else f"{trend}%"
            else:
                player["trend"] = "—"
            players.append(player)
        return players

    def get_all_team_rosters(self) -> dict[str, list]:
        """Returns every team's roster keyed by team_id string."""
        url = f"{BASE_URL}/league/{self.league_key}/teams;out=roster/players"
        root = self._get_xml(url)
        teams = {}
        for team in root.findall(".//team"):
            team_id = _text(team, "team_id")
            players = [_parse_player(p) for p in team.findall(".//player")]
            teams[team_id] = players
        return teams


# ── Helpers ──────────────────────────────────────────────────────────────────

def _text(element: ET.Element, path: str) -> str | None:
    """Safe text extraction from an XML element."""
    node = element.find(path)
    return node.text if node is not None else None


def _parse_player(p: ET.Element) -> dict:
    """Extract a normalised player dict from a <player> XML element."""
    return {
        "yahoo_id": _text(p, "player_id"),
        "name": _text(p, ".//name/full"),
        "first_name": _text(p, ".//name/first") or "",
        "last_name": _text(p, ".//name/last") or "",
        "position": _text(p, ".//display_position"),
        "primary_position": _text(p, ".//primary_position"),
        "eligible_positions": [
            pos.text
            for pos in p.findall(".//eligible_positions//position")
            if pos.text
        ],
        "mlb_team": _text(p, ".//editorial_team_abbr"),
        "status": _text(p, ".//status"),
        "injury_note": _text(p, ".//injury_note"),
    }
