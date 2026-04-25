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
from datetime import date, datetime, timedelta
from requests.auth import HTTPBasicAuth
from typing import Optional
from xml.etree import ElementTree as ET

from src.config import (
    YAHOO_TOKEN_PATH, YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET,
    YAHOO_LEAGUE_ID, YAHOO_GAME_KEY, YAHOO_TEAM_ID,
)

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
BASE_URL  = "https://fantasysports.yahooapis.com/fantasy/v2"
NS        = "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"

IL_STATUSES  = {"IL", "DL", "IR", "DTD", "NA"}
IL_POSITIONS = {"IL", "IL10", "IL60", "DL", "DL15", "DL60", "NA"}

# Fallback mapping used only if data/managers.json is missing AND the Yahoo
# sync fails. This table is known-stale for 2026 — sync_managers() overwrites
# data/managers.json on every daily run so this fallback rarely fires.
_FALLBACK_TEAM_MAP = {
    "1": "WIZ", "2": "B2J", "3": "CFL", "4": "HAM",
    "5": "JEP", "6": "LFB", "7": "LAW", "8": "SAD",
    "9": "DRO", "10": "RV", "11": "TBB", "12": "WAR"
}

MANAGERS_JSON_PATH = os.getenv("MANAGERS_JSON_PATH", "data/managers.json")


def _load_team_map() -> dict:
    """
    Returns team_id (str) → abbr (str) mapping.
    Handles two schemas:
      1. fbp-trade-bot: {"teams": {"WAR": {"yahoo_team_id": "12", ...}}}
      2. flat:          {"12": "WAR"} or {"12": {"abbr": "WAR"}}
    Falls back to _FALLBACK_TEAM_MAP if file missing or unreadable.
    """
    try:
        if os.path.exists(MANAGERS_JSON_PATH):
            with open(MANAGERS_JSON_PATH) as f:
                data = json.load(f)

            # Schema 1: fbp-trade-bot authoritative format
            if "teams" in data:
                out = {}
                for abbr, info in data["teams"].items():
                    if isinstance(info, dict):
                        tid = info.get("yahoo_team_id")
                        if tid and abbr != "_comment":
                            out[str(tid)] = abbr
                if out:
                    print(f"  🗂️  managers.json: loaded {len(out)} teams (fbp-trade-bot schema)")
                    return out

            # Schema 2: flat {team_id: abbr} or {team_id: {abbr: ...}}
            out = {}
            for k, v in data.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict):
                    abbr = v.get("abbr") or v.get("short")
                else:
                    abbr = v
                if abbr:
                    out[str(k)] = str(abbr)
            if out:
                print(f"  🗂️  managers.json: loaded {len(out)} teams (flat schema)")
                return out

    except Exception as e:
        print(f"  ⚠️  managers.json load error: {e} — using fallback map")

    print("  ⚠️  managers.json missing or empty — using hardcoded fallback map")
    return dict(_FALLBACK_TEAM_MAP)


# Backwards-compat reference. Code should prefer calling _load_team_map().
YAHOO_TEAM_MAP = _load_team_map()


def _derive_abbr_from_name(name: str, team_id: str) -> str:
    """
    Best-effort short abbr extraction from a Yahoo team name.
    Rules, in order:
      1. Token inside parentheses, 2-5 chars, all uppercase/alnum → that token
      2. Leading all-caps alnum token 2-5 chars → that token
      3. Trailing all-caps alnum token 2-5 chars → that token
      4. First 3 letters of first word, uppercased
      5. "T{team_id}"
    """
    import re
    if not name:
        return f"T{team_id}"
    m = re.search(r"\(([A-Z0-9]{2,5})\)", name)
    if m:
        return m.group(1)
    tokens = re.findall(r"[A-Za-z0-9]+", name)
    if tokens:
        if re.fullmatch(r"[A-Z0-9]{2,5}", tokens[0]):
            return tokens[0]
        if re.fullmatch(r"[A-Z0-9]{2,5}", tokens[-1]):
            return tokens[-1]
        return tokens[0][:3].upper()
    return f"T{team_id}"


class YahooClient:
    def __init__(self):
        self.client_id     = YAHOO_CLIENT_ID
        self.client_secret = YAHOO_CLIENT_SECRET
        self.league_id     = YAHOO_LEAGUE_ID
        self.game_key      = YAHOO_GAME_KEY
        self.team_id       = YAHOO_TEAM_ID
        self._token_data   = self._load_token()
        self._week_schedule_cache: Optional[dict] = None

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
            self._token_data["expires_at"] = 0
            resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return ET.fromstring(resp.text)

    # ── Original public API methods ───────────────────────────────────────────

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

    def sync_managers(self, force: bool = False) -> dict:
        """
        Fallback-only team-id → abbr sync. If authoritative managers.json is
        already present with a full team set, trust it and skip the Yahoo call.
        Yahoo sync only reliably returns a partial team list and would destroy
        the fbp-trade-bot mapping.

        Pass force=True to regenerate regardless (diagnostic use only).
        Returns the in-memory {team_id: abbr} mapping either way.
        """
        global YAHOO_TEAM_MAP
        # If authoritative file already has the full team set, trust it.
        # Do NOT overwrite with Yahoo-derived abbrs — Yahoo sync only returns
        # partial results and would destroy the fbp-trade-bot mapping.
        if not force:
            current = _load_team_map()
            if len(current) >= 10:
                print(f"  🗂️  sync_managers: {len(current)} teams in managers.json — using authoritative file")
                YAHOO_TEAM_MAP = current
                return current

        # File missing or partial — attempt Yahoo sync as fallback
        try:
            url  = f"{BASE_URL}/league/{self.league_key}/teams"
            root = self._get_xml(url)
        except Exception as e:
            print(f"  ⚠️  sync_managers: Yahoo fetch failed ({e}) — keeping existing map")
            return _load_team_map()

        mapping = {}
        for team in root.findall(f".//{{{NS}}}team"):
            tid  = _text(team, f"{{{NS}}}team_id") or ""
            name = _text(team, f".//{{{NS}}}name") or ""
            if not tid:
                continue
            abbr = _derive_abbr_from_name(name, tid)
            mapping[tid] = {"abbr": abbr, "name": name}

        if not mapping:
            print("  ⚠️  sync_managers: no teams parsed — keeping existing map")
            return _load_team_map()

        try:
            os.makedirs(os.path.dirname(MANAGERS_JSON_PATH) or ".", exist_ok=True)
            with open(MANAGERS_JSON_PATH, "w") as f:
                json.dump(mapping, f, indent=2, sort_keys=True)
            YAHOO_TEAM_MAP = {tid: v["abbr"] for tid, v in mapping.items()}
            print(f"  🗂️  sync_managers: wrote auto-derived {len(mapping)} teams to "
                  f"{MANAGERS_JSON_PATH} (abbr heuristic — override with the fbp-trade-bot file)")
        except Exception as e:
            print(f"  ⚠️  sync_managers: write failed ({e})")

        return {tid: v["abbr"] for tid, v in mapping.items()}

    # ── Weekly matchup engine methods ─────────────────────────────────────────

    def get_current_matchup_full(self) -> dict:
        """
        Returns current week's category stats for both teams plus
        remaining game counts.

        Pitching rate stats (ERA, K/9, H/9, BB/9) are NOT fetched directly —
        they are derived inside aggregate_pitching_line() from the raw
        component counts (ER, K, H_allowed, BB_allowed, IP) that ARE available.

        Key naming conventions to avoid stat_id collisions:
          K        → pitching strikeouts   (stat_id 62)
          K_hit    → batting strikeouts    (stat_id 27)
          HR       → pitching HR allowed   (stat_id 59)
          HR_hit   → batting home runs     (stat_id 16)

        Returns:
            {
                "my_stats":  {APP, IP, ER, HR, K, QS, H_allowed, BB_allowed,
                              R, H, HR_hit, RBI, SB, BB, K_hit, AVG, OPS},
                "opp_stats": same,
                "my_remaining_games":  int,
                "opp_remaining_games": int,
            }
        """
        url  = f"{BASE_URL}/team/{self.team_key}/matchups"
        root = self._get_xml(url)

        matchups = root.findall(f".//{{{NS}}}matchup")
        print(f"  🔍 matchup XML: {len(matchups)} matchup node(s) found")

        # Prefer is_current_week==1/"1"; fallback to today-in-range, else last.
        def _is_current(m):
            flag = _text(m, f"{{{NS}}}is_current_week") or ""
            if flag.strip() in ("1", "true", "True"):
                return True
            # Fallback: today between week_start and week_end inclusive
            start = _text(m, f"{{{NS}}}week_start") or ""
            end   = _text(m, f"{{{NS}}}week_end")   or ""
            try:
                if start and end:
                    sd = datetime.strptime(start, "%Y-%m-%d").date()
                    ed = datetime.strptime(end,   "%Y-%m-%d").date()
                    return sd <= date.today() <= ed
            except ValueError:
                return False
            return False

        current = next((m for m in matchups if _is_current(m)),
                       matchups[-1] if matchups else None)
        if current is None:
            print("  ⚠️  matchup XML: no matchups at all")
            return {}

        wk  = _text(current, f"{{{NS}}}week") or "?"
        icw = _text(current, f"{{{NS}}}is_current_week") or ""
        print(f"  🔍 selected matchup: week={wk} is_current_week={icw!r}")

        result       = {"my_stats": {}, "opp_stats": {}}
        opponent_tid = None

        # Use direct <teams>/<team> children; .//team leaks nested team nodes.
        team_nodes = current.findall(f"./{{{NS}}}teams/{{{NS}}}team")
        if not team_nodes:   # belt-and-braces fallback
            team_nodes = current.findall(f".//{{{NS}}}team")
        print(f"  🔍 team nodes in matchup: {len(team_nodes)}")

        for team in team_nodes:
            tid = _text(team, f"{{{NS}}}team_id")
            if tid != str(self.team_id):
                opponent_tid = tid
            key = "my_stats" if tid == str(self.team_id) else "opp_stats"
            stat_nodes = team.findall(f".//{{{NS}}}stat")
            parsed = 0
            preview = []
            for stat in stat_nodes:
                stat_id = _text(stat, f"{{{NS}}}stat_id")
                value   = _text(stat, f"{{{NS}}}value")
                name    = _STAT_ID_MAP.get(stat_id)
                if len(preview) < 25:
                    preview.append(f"{stat_id}={value!r}")
                if name and value not in (None, "-", ""):
                    try:
                        result[key][name] = float(value)
                        parsed += 1
                    except ValueError:
                        pass
                elif not name and stat_id and value not in (None, "-", ""):
                    # Unrecognized stat_id — log so we can add to _STAT_ID_MAP.
                    print(f"     ⚠️  unrecognized stat_id={stat_id} value={value!r}")
            print(f"     team_id={tid} ({key}): {len(stat_nodes)} stat nodes, {parsed} parsed. Preview: {preview}")

        print(f"  🔍 my_stats:  {result['my_stats']}")
        print(f"  🔍 opp_stats: {result['opp_stats']}")

        # Opponent name (read fresh from managers.json)
        team_map = _load_team_map()
        opponent_name = team_map.get(str(opponent_tid), "OPP") if opponent_tid else "OPP"
        print(f"  🔍 opponent: team_id={opponent_tid} → {opponent_name}")

        # Count cats currently winning from banked stats
        DIRECT_SCORING = {
            "R": True, "H": True, "HR_hit": True, "RBI": True,
            "SB": True, "BB": True, "AVG": True, "OPS": True,
            "K_hit": False, "APP": True, "K": True, "QS": True,
            "ER": False, "HR": False,
        }
        cats_you = cats_opp = 0
        for cat, higher in DIRECT_SCORING.items():
            yv = result["my_stats"].get(cat, 0) or 0
            ov = result["opp_stats"].get(cat, 0) or 0
            if higher:
                if yv > ov:   cats_you += 1
                elif ov > yv: cats_opp += 1
            else:
                if yv < ov:   cats_you += 1
                elif ov < yv: cats_opp += 1

        yesterday = date.today() - timedelta(days=1)
        result["opponent_team_name"] = opponent_name
        result["current_score_you"]  = cats_you
        result["current_score_opp"]  = cats_opp
        result["score_as_of"]        = yesterday.strftime("%a %b %-d")

        result["my_remaining_games"]  = self._remaining_games_this_week(False)
        result["opp_remaining_games"] = self._remaining_games_this_week(True)
        return result

    def get_pitchers_with_remaining_starts(self,
                                            is_opponent: bool = False
                                            ) -> list[dict]:
        """
        Returns SP + RP on the roster who have at least one game
        remaining this week.

        Each dict: {name, team, yahoo_id, position, opponent, opp_rank}
        opp_rank left at 15 (neutral); overwritten by engine.
        """
        team_id = self._opponent_team_id() if is_opponent else self.team_id
        url     = f"{BASE_URL}/team/{self.league_key}.t.{team_id}/roster/players"
        root    = self._get_xml(url)

        pitchers = []
        for p in root.findall(f".//{{{NS}}}player"):
            pos = _text(p, f"{{{NS}}}display_position") or ""
            if not any(x in pos for x in ("SP", "RP", "P")):
                continue
            if pos in ("OF", "1B", "2B", "3B", "SS", "C", "DH", "UTIL"):
                continue

            # Skip IL-slotted or injured players
            status  = _text(p, f"{{{NS}}}status") or ""
            sel_pos = _text(p, f".//{{{NS}}}selected_position/{{{NS}}}position") or ""
            if status in IL_STATUSES or sel_pos in IL_POSITIONS:
                name = _text(p, f".//{{{NS}}}full") or "unknown"
                print(f"  ⏭️  Skipping {name} — IL/injured (status={status!r}, slot={sel_pos!r})")
                continue

            name     = _text(p, f".//{{{NS}}}full") or ""
            yahoo_id = _text(p, f"{{{NS}}}player_id") or ""
            mlb_team = _text(p, f"{{{NS}}}editorial_team_abbr") or ""

            game_info = self._next_game_this_week(mlb_team)
            if game_info:
                dates     = game_info.get("dates", [])
                game_date = dates[0] if dates else ""
                pitchers.append({
                    "name":      name,
                    "team":      mlb_team,
                    "yahoo_id":  yahoo_id,
                    "position":  pos,
                    "opponent":  game_info.get("opponent", ""),
                    "opp_rank":  15,
                    "game_date": game_date,
                })

        return pitchers

    def get_fa_pitchers_with_starts(self, count: int = 25) -> list[dict]:
        """
        FA starting pitchers with at least one start remaining this week.
        Returns list of {name, team, yahoo_id, position, opponent_today}.
        """
        url  = (
            f"{BASE_URL}/league/{self.league_key}/players"
            f";status=FA;position=SP;sort=OR;count={count}"
        )
        root = self._get_xml(url)

        result = []
        for p in root.findall(f".//{{{NS}}}player"):
            name     = _text(p, f".//{{{NS}}}full") or ""
            yahoo_id = _text(p, f"{{{NS}}}player_id") or ""
            mlb_team = _text(p, f"{{{NS}}}editorial_team_abbr") or ""
            pos      = _text(p, f"{{{NS}}}display_position") or "SP"

            # Skip IL/injured free agents
            status  = _text(p, f"{{{NS}}}status") or ""
            sel_pos = _text(p, f".//{{{NS}}}selected_position/{{{NS}}}position") or ""
            if status in IL_STATUSES or sel_pos in IL_POSITIONS:
                continue

            game_info = self._next_game_this_week(mlb_team)
            if game_info:
                result.append({
                    "name":           name,
                    "team":           mlb_team,
                    "yahoo_id":       yahoo_id,
                    "position":       pos,
                    "opponent_today": game_info.get("opponent", ""),
                })

        return result

    def get_team_rolling_hitting_stats(self,
                                        is_opponent: bool = False,
                                        mlb_id_map: dict = None,
                                        mlb_client=None,
                                        days: int = 21) -> dict:
        """
        Team-level rolling hitting totals over last `days` days.
        Sums byDateRange hitting stats across rostered batters.

        Returns dict with raw totals, AVG/OPS, days_in_window,
        and banked_* keys for stats already accumulated this week.
        """
        team_id    = self._opponent_team_id() if is_opponent else self.team_id
        batters    = self._get_roster_batters(team_id)
        end_date   = date.today()
        start_date = end_date - timedelta(days=days)
        start_str  = start_date.strftime("%Y-%m-%d")
        end_str    = end_date.strftime("%Y-%m-%d")

        totals   = {c: 0.0 for c in ("R", "H", "HR_hit", "RBI", "SB", "BB", "K_hit", "TB_hit")}
        ab_sum   = 0.0
        pa_sum   = 0.0
        obp_x_pa = 0.0
        slg_x_ab = 0.0

        if mlb_id_map and mlb_client:
            for name in batters:
                mlb_id = mlb_id_map.get(name)
                if not mlb_id:
                    continue
                stats = mlb_client.get_batter_date_range_stats(
                    mlb_id, start_str, end_str
                )
                if not stats:
                    continue
                totals["R"]     += stats.get("R",   0)
                totals["H"]     += stats.get("H",   0)
                totals["HR_hit"]+= stats.get("HR",  0)
                totals["RBI"]   += stats.get("RBI", 0)
                totals["SB"]    += stats.get("SB",  0)
                totals["BB"]    += stats.get("BB",  0)
                totals["K_hit"] += stats.get("K",   0)
                totals["TB_hit"] += stats.get("TB",  0)
                ab       = stats.get("AB", 0)
                pa       = stats.get("PA", 0)
                ab_sum  += ab
                pa_sum  += pa
                obp_x_pa += stats.get("OBP", 0.315) * pa
                slg_x_ab += stats.get("SLG", 0.400) * ab

        avg = round(totals["H"]  / ab_sum,  3) if ab_sum > 0 else 0.248
        obp = round(obp_x_pa     / pa_sum,  3) if pa_sum > 0 else 0.315
        slg = round(slg_x_ab     / ab_sum,  3) if ab_sum > 0 else 0.400
        ops = round(obp + slg, 3)

        try:
            matchup = self.get_current_matchup_full()
            bk      = matchup.get("opp_stats" if is_opponent else "my_stats", {})
        except Exception:
            bk = {}

        return {
            "R":      totals["R"],
            "H":      totals["H"],
            "HR_hit": totals["HR_hit"],
            "RBI":    totals["RBI"],
            "SB":     totals["SB"],
            "BB":     totals["BB"],
            "K_hit":  totals["K_hit"],
            "TB_hit": totals["TB_hit"],
            "AVG": avg, "OPS": ops,
            "days_in_window": days,
            "banked_R":       bk.get("R",      0),
            "banked_H":       bk.get("H",      0),
            "banked_HR_hit":  bk.get("HR_hit", 0),
            "banked_RBI":     bk.get("RBI",    0),
            "banked_SB":      bk.get("SB",     0),
            "banked_BB":      bk.get("BB",     0),
            "banked_K_hit":   bk.get("K_hit",  0),
            "banked_TB_hit":  bk.get("TB_hit", 0),  # Yahoo doesn't expose hitting TB; always 0
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _next_game_this_week(self, mlb_team_abbr: str) -> Optional[dict]:
        """
        Returns schedule info dict if the team has a game remaining
        this week, else None. Caches week schedule on first call.
        """
        if self._week_schedule_cache is None:
            self._week_schedule_cache = _build_week_schedule()
        return self._week_schedule_cache.get(mlb_team_abbr.upper())

    def _remaining_games_this_week(self, is_opponent: bool = False) -> int:
        """
        Count distinct remaining game dates for a team's roster MLB teams.
        """
        team_id = self._opponent_team_id() if is_opponent else self.team_id
        url     = f"{BASE_URL}/team/{self.league_key}.t.{team_id}/roster/players"
        try:
            root = self._get_xml(url)
        except Exception:
            return 3

        mlb_teams = set()
        for p in root.findall(f".//{{{NS}}}player"):
            t = _text(p, f"{{{NS}}}editorial_team_abbr")
            if t:
                mlb_teams.add(t.upper())

        if self._week_schedule_cache is None:
            self._week_schedule_cache = _build_week_schedule()

        game_dates = set()
        for abbr, info in self._week_schedule_cache.items():
            if abbr in mlb_teams:
                for gd in info.get("dates", []):
                    game_dates.add(gd)

        return max(1, len(game_dates)) if game_dates else 3

    def _opponent_team_id(self) -> str:
        """Find the opponent team ID from the current week's matchup."""
        url  = f"{BASE_URL}/team/{self.team_key}/matchups"
        root = self._get_xml(url)
        matchups = root.findall(f".//{{{NS}}}matchup")
        current  = next(
            (m for m in matchups
             if _text(m, f"{{{NS}}}is_current_week") == "1"),
            matchups[-1] if matchups else None,
        )
        if not current:
            return "1"
        for team in current.findall(f".//{{{NS}}}team"):
            tid = _text(team, f"{{{NS}}}team_id")
            if tid != str(self.team_id):
                return tid
        return "1"

    def _get_roster_batters(self, team_id) -> list[str]:
        """Batter names on the roster — excludes pure SP/RP/P slots."""
        url = f"{BASE_URL}/team/{self.league_key}.t.{team_id}/roster/players"
        try:
            root = self._get_xml(url)
        except Exception:
            return []
        batters = []
        for p in root.findall(f".//{{{NS}}}player"):
            pos  = _text(p, f"{{{NS}}}display_position") or ""
            name = _text(p, f".//{{{NS}}}full") or ""
            if name and pos not in ("SP", "RP", "P"):
                batters.append(name)
        return batters


# ── Module-level helpers ──────────────────────────────────────────────────────

def _build_week_schedule() -> dict:
    """
    Fetch remaining games this week from MLB Stats API.
    Yahoo weeks run Monday–Sunday; fetches today through Sunday.

    Returns dict keyed by uppercase MLB team abbreviation:
        {"NYY": {"opponent": "BOS", "dates": ["2026-04-03", "2026-04-04"]}}
    """
    today       = date.today()
    days_to_sun = 6 - today.weekday()   # Monday=0, Sunday=6
    week_end    = today + timedelta(days=days_to_sun)
    start_str   = today.strftime("%Y-%m-%d")
    end_str     = week_end.strftime("%Y-%m-%d")

    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&startDate={start_str}&endDate={end_str}&hydrate=team"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️  Week schedule fetch failed: {e}")
        return {}

    schedule: dict = {}

    for day in data.get("dates", []):
        game_date = day.get("date", "")
        for game in day.get("games", []):
            status = game.get("status", {}).get("abstractGameState", "")
            if status in ("Final", "Postponed"):
                continue
            for side, opp_side in [("home", "away"), ("away", "home")]:
                team_info = game.get("teams", {}).get(side, {}).get("team", {})
                opp_info  = game.get("teams", {}).get(opp_side, {}).get("team", {})
                abbr      = team_info.get("abbreviation", "").upper()
                opp_abbr  = opp_info.get("abbreviation",  "").upper()
                if not abbr:
                    continue
                if abbr not in schedule:
                    schedule[abbr] = {"opponent": opp_abbr, "dates": []}
                if game_date not in schedule[abbr]["dates"]:
                    schedule[abbr]["dates"].append(game_date)

    return schedule


def _text(element: ET.Element, path: str) -> Optional[str]:
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


# ── Yahoo stat ID map ─────────────────────────────────────────────────────────
# Authoritative source: league 8560's stat_categories from
# /league/{league_key}/settings, mirrored in fbp-trade-bot's
# data/standings.json under "stat_categories". The previous map (sourced
# from calculate_baselines.py) had nearly every non-trivial ID wrong —
# e.g. it called stat_id 12 "R" when Yahoo emits 12 = HR. That's why the
# pitching column showed all zeros and K_hit/TB_hit never populated.
#
# Naming conventions to avoid display collisions inside the engine:
#   K       = pitching strikeouts   (stat_id 42)
#   K_hit   = batting strikeouts    (stat_id 21)
#   HR      = pitching HR allowed   (stat_id 38)
#   HR_hit  = batting home runs     (stat_id 12)
#   TB      = pitching TB allowed   (stat_id 49)
#   TB_hit  = batting total bases   (stat_id 23)
#
# ERA, K/9, H/9, BB/9 are scored DIRECTLY by this league — they come back
# as banked rate values from Yahoo. aggregate_pitching_line() still derives
# them for projections (which only have raw components), so we also back-
# derive banked H_allowed/BB_allowed from banked H/9 × IP / 9 there.
_STAT_ID_MAP = {
    # ── Hitting (league scoring categories) ──────────────────────────────────
    "7":   "R",
    "8":   "H",
    "12":  "HR_hit",       # home runs (batting)
    "13":  "RBI",
    "16":  "SB",
    "18":  "BB",           # walks drawn
    "21":  "K_hit",        # strikeouts (batting)
    "23":  "TB_hit",       # total bases (batting)
    "3":   "AVG",
    "55":  "OPS",
    # 60 = H/AB display-only — intentionally not mapped

    # ── Pitching (league scoring categories) ─────────────────────────────────
    "24":  "APP",          # pitching appearances
    "50":  "IP",           # innings pitched (display-only but always present)
    "37":  "ER",
    "38":  "HR",           # HR allowed (pitching)
    "42":  "K",            # strikeouts (pitching)
    "49":  "TB",           # total bases allowed
    "26":  "ERA",          # direct — no longer derived for banked
    "57":  "K/9",          # direct
    "77":  "H/9",          # direct
    "78":  "BB/9",         # direct
    "83":  "QS",
}
