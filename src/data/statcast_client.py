"""
Statcast data via pybaseball.
Pulls hitter leading indicators from Baseball Savant.
"""
from datetime import date, timedelta
import pandas as pd

try:
    from pybaseball import statcast_batter, playerid_lookup
except ImportError:
    raise ImportError("Run: pip install pybaseball")

from src.config import STATCAST_ROLLING_DAYS


class StatcastClient:

    def get_hitter_metrics(self, first_name: str, last_name: str) -> dict:
        """
        Returns rolling Statcast metrics for a hitter.
        Key signals: barrel_rate, exit_velocity, whiff_rate, xba, walk_rate.
        """
        end = date.today()
        start = end - timedelta(days=STATCAST_ROLLING_DAYS)

        lookup = playerid_lookup(last_name, first_name)
        if lookup.empty:
            return {}

        player_id = int(lookup.iloc[0]["key_mlbam"])
        df = statcast_batter(start.isoformat(), end.isoformat(), player_id=player_id)
        if df.empty:
            return {}

        return {
            "name": f"{first_name} {last_name}",
            "player_id": player_id,
            "pa": len(df),
            "avg_exit_velocity": round(df["launch_speed"].dropna().mean(), 1),
            "barrel_rate": round((df["barrel"].sum() / len(df)) * 100, 1)
                           if "barrel" in df.columns else None,
            "xba": round(df["estimated_ba_using_speedangle"].dropna().mean(), 3)
                   if "estimated_ba_using_speedangle" in df.columns else None,
            "whiff_rate": _calc_whiff_rate(df),
            "walk_rate": _calc_walk_rate(df),
        }

    def get_breakout_signals(self, roster: list[dict]) -> list[dict]:
        """
        Given rostered hitters, returns those with strong positive signals
        ordered by signal score.
        """
        signals = []
        for player in roster:
            if player.get("position") in ("SP", "RP"):
                continue
            metrics = self.get_hitter_metrics(
                player.get("first_name", ""),
                player.get("last_name", "")
            )
            if not metrics:
                continue
            score = _compute_signal_score(metrics)
            if score > 0:
                signals.append({**metrics, "signal_score": score})
        return sorted(signals, key=lambda x: x["signal_score"], reverse=True)


def _calc_whiff_rate(df: pd.DataFrame) -> float | None:
    if "description" not in df.columns:
        return None
    swings = df[df["description"].isin(
        ["swinging_strike", "foul", "hit_into_play", "foul_tip"]
    )]
    whiffs = df[df["description"] == "swinging_strike"]
    if len(swings) == 0:
        return None
    return round(len(whiffs) / len(swings) * 100, 1)


def _calc_walk_rate(df: pd.DataFrame) -> float | None:
    if "events" not in df.columns:
        return None
    pa = df[df["events"].notna()]
    walks = pa[pa["events"] == "walk"]
    if len(pa) == 0:
        return None
    return round(len(walks) / len(pa) * 100, 1)


def _compute_signal_score(metrics: dict) -> float:
    score = 0.0
    if metrics.get("barrel_rate") and metrics["barrel_rate"] > 10:
        score += 2.0
    if metrics.get("avg_exit_velocity") and metrics["avg_exit_velocity"] > 92:
        score += 1.5
    if metrics.get("walk_rate") and metrics["walk_rate"] > 10:
        score += 1.0
    if metrics.get("whiff_rate") and metrics["whiff_rate"] < 20:
        score += 1.0
    if metrics.get("xba") and metrics.get("ba"):
        if metrics["xba"] - metrics["ba"] > 0.030:
            score += 2.0
    return score
