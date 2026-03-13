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
