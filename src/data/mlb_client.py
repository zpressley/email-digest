"""
MLB Stats API client.
Free public API — no auth required.
Base: https://statsapi.mlb.com/api/v1
"""
import requests
from datetime import date, timedelta

BASE_URL = "https://statsapi.mlb.com/api/v1"


class MLBClient:

    def get_schedule(self, target_date: date = None) -> list[dict]:
        """Returns all games for a given date (defaults to today)."""
        if target_date is None:
            target_date = date.today()
        date_str = target_date.strftime("%Y-%m-%d")
        url = f"{BASE_URL}/schedule?sportId=1&date={date_str}&hydrate=probablePitcher,lineups"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        games = []
        for game_date in resp.json().get("dates", []):
            games.extend(game_date.get("games", []))
        return games

    def get_probable_starters(self, days_ahead: int = 5) -> list[dict]:
        """
        Returns probable starters for the next N days.
        Flags confirmed vs. inferred (4+ days rest logic).
        """
        starters = []
        today = date.today()
        for i in range(days_ahead):
            target = today + timedelta(days=i)
            games = self.get_schedule(target)
            for game in games:
                for side in ["home", "away"]:
                    probable = game.get("teams", {}).get(side, {}).get("probablePitcher")
                    if probable:
                        opp_side = "home" if side == "away" else "away"
                        starters.append({
                            "player_id": probable.get("id"),
                            "name": probable.get("fullName"),
                            "team": game["teams"][side]["team"]["name"],
                            "opponent": game["teams"][opp_side]["team"]["name"],
                            "game_date": target.isoformat(),
                            "days_out": i,
                            "confirmed": True,
                        })
        return starters

    def get_team_offense_rankings(self, days: int = 14) -> list[dict]:
        """
        Returns teams ranked by runs scored over last N days.
        Used by matchup finder.
        """
        # TODO: rolling team stats from /teams/stats
        raise NotImplementedError

    # ── Weekly matchup engine methods ─────────────────────────────────────────

    def get_pitcher_game_log(self, player_id: int, season: int) -> list[dict]:
        """
        Game-by-game pitching log for a player.
        Returns list of split dicts; each has 'stat' and 'date' keys.
        Used by weekly_matchup_engine to build appearance distributions
        for both SP and RP (all appearances included, no role filter).

        stat keys: inningsPitched, strikeOuts, earnedRuns,
                   homeRuns, hits, baseOnBalls, gamesStarted, gamesPlayed
        NOTE: totalBases is NOT available here — use get_pitcher_season_stats().
        """
        url = (
            f"{BASE_URL}/people/{player_id}/stats"
            f"?stats=gameLog&group=pitching&season={season}"
        )
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data  = resp.json()
            stats = data.get("stats", [])
            if stats:
                return stats[0].get("splits", [])
        except Exception as e:
            print(f"  ⚠️  Game log failed for player {player_id} season {season}: {e}")
        return []

    def get_pitcher_season_stats(self, player_id: int, season: int) -> dict | None:
        """
        Season-level pitching stats. Used to pull:
          - totalBases (TB allowed) — available here, NOT in game log
          - Season APP for RP workload rate
          - Season IP for TB/IP rate derivation

        Returns flat dict: {IP, ER, K, HR, H, BB, TB, APP, GS, ERA, WHIP}
        or None if no data.
        """
        url = (
            f"{BASE_URL}/people/{player_id}/stats"
            f"?stats=season&group=pitching&season={season}"
        )
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ⚠️  Season stats failed for player {player_id} season {season}: {e}")
            return None

        for sg in data.get("stats", []):
            splits = sg.get("splits", [])
            if not splits:
                continue
            stat = splits[0].get("stat", {})
            ip   = _parse_ip_str(str(stat.get("inningsPitched", "0.0")))
            return {
                "IP":   ip,
                "ER":   stat.get("earnedRuns",    0),
                "K":    stat.get("strikeOuts",     0),
                "HR":   stat.get("homeRuns",       0),
                "H":    stat.get("hits",           0),
                "BB":   stat.get("baseOnBalls",    0),
                "TB":   stat.get("totalBases",     0),
                "APP":  stat.get("gamesPlayed",    0),
                "GS":   stat.get("gamesStarted",   0),
                "ERA":  float(stat.get("era",  0.0) or 0.0),
                "WHIP": float(stat.get("whip", 0.0) or 0.0),
            }
        return None

    def get_batter_date_range_stats(self, player_id: int,
                                     start_date: str, end_date: str) -> dict | None:
        """
        A batter's hitting stats over a date range.
        Used by yahoo_client.get_team_rolling_hitting_stats().

        Returns flat dict: {R, H, HR, RBI, SB, BB, K, TB, AB, PA, AVG, OBP, SLG}
        or None if no data.
        """
        url = (
            f"{BASE_URL}/people/{player_id}/stats"
            f"?stats=byDateRange&startDate={start_date}&endDate={end_date}"
            f"&group=hitting"
        )
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ⚠️  Date range stats failed for player {player_id}: {e}")
            return None

        for sg in data.get("stats", []):
            splits = sg.get("splits", [])
            if not splits:
                continue
            stat = splits[0].get("stat", {})
            return {
                "R":   stat.get("runs",            0),
                "H":   stat.get("hits",            0),
                "HR":  stat.get("homeRuns",         0),
                "RBI": stat.get("rbi",              0),
                "SB":  stat.get("stolenBases",      0),
                "BB":  stat.get("baseOnBalls",      0),
                "K":   stat.get("strikeOuts",       0),
                "TB":  stat.get("totalBases",       0),
                "AB":  stat.get("atBats",           0),
                "PA":  stat.get("plateAppearances", 0),
                "AVG": float(stat.get("avg",  0.0) or 0.0),
                "OBP": float(stat.get("obp",  0.0) or 0.0),
                "SLG": float(stat.get("slg",  0.0) or 0.0),
            }
        return None

    def get_player_recent_stats(self, player_id: int, days: int = 7) -> dict:
        """Returns a player's stats over the last N days."""
        end = date.today()
        start = end - timedelta(days=days)
        url = (
            f"{BASE_URL}/people/{player_id}/stats"
            f"?stats=byDateRange&startDate={start}&endDate={end}"
            f"&group=hitting,pitching"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_minor_league_stats(self, player_id: int) -> dict:
        """Returns minor league stats for a player."""
        url = (
            f"{BASE_URL}/people/{player_id}/stats"
            f"?stats=season&group=hitting,pitching&sportId=11,12,13,14"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()


def _parse_ip_str(ip_str: str) -> float:
    """Convert MLB innings string '6.2' (6 full + 2 outs) → 6.667 decimal."""
    try:
        parts = str(ip_str).split(".")
        return int(parts[0]) + (int(parts[1]) if len(parts) > 1 else 0) / 3
    except (ValueError, IndexError):
        return 0.0
