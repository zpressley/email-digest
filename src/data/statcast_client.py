"""
Statcast data via pybaseball.
Uses MLB ID from combined_players.json — no name lookup guesswork.

combined_players.json is the single source of truth for all player IDs.
mlb_id field maps directly to Baseball Savant's player ID system.
"""
import os
import json
from datetime import date, timedelta
import pandas as pd

try:
    from pybaseball import statcast_batter
except ImportError:
    raise ImportError("Run: pip install pybaseball")

from src.config import STATCAST_ROLLING_DAYS, COMBINED_PLAYERS_PATH


class StatcastClient:

    def __init__(self):
        self._id_cache = self._build_id_cache()

    def _build_id_cache(self) -> dict[str, int]:
        """
        Build a name → mlb_id lookup from combined_players.json.
        Used to resolve MLB IDs for any player on the roster.
        """
        try:
            with open(COMBINED_PLAYERS_PATH) as f:
                players = json.load(f)
            cache = {}
            for p in players:
                name = p.get("name", "").strip()
                mlb_id = p.get("mlb_id")
                if name and mlb_id:
                    try:
                        cache[name.lower()] = int(mlb_id)
                    except (ValueError, TypeError):
                        continue
            print(f"  📊 Statcast ID cache: {len(cache)} players loaded")
            return cache
        except FileNotFoundError:
            print(f"⚠️  combined_players.json not found — Statcast disabled")
            return {}

    def get_mlb_id(self, player_name: str) -> int | None:
        """
        Resolve MLB ID from player name via combined_players.json cache.
        Tries exact match first then falls back to last name match.
        """
        name_lower = player_name.lower().strip()

        # Exact match
        if name_lower in self._id_cache:
            return self._id_cache[name_lower]

        # Last name match — handles minor name format differences
        last_name = name_lower.split()[-1] if name_lower else ""
        for cached_name, mlb_id in self._id_cache.items():
            if cached_name.endswith(last_name):
                return mlb_id

        return None

    def get_hitter_metrics(self, player_name: str, mlb_id: int | None = None) -> dict:
        """
        Returns rolling Statcast metrics for a hitter.
        Accepts mlb_id directly (preferred) or resolves from player_name.

        Key signals:
            barrel_rate        — rising = power incoming
            avg_exit_velocity  — rising = better contact quality
            xba                — if >> actual BA, regression upward likely
            whiff_rate         — falling = better contact
            walk_rate          — rising = better plate discipline
        """
        end   = date.today()
        start = end - timedelta(days=STATCAST_ROLLING_DAYS)

        # Resolve MLB ID
        if not mlb_id:
            mlb_id = self.get_mlb_id(player_name)
        if not mlb_id:
            return {}

        try:
            df = statcast_batter(
                start.isoformat(),
                end.isoformat(),
                player_id=mlb_id,
            )
        except Exception as e:
            print(f"  ⚠️  Statcast fetch failed for {player_name} ({mlb_id}): {e}")
            return {}

        if df is None or df.empty:
            return {}

        return {
            "name":               player_name,
            "mlb_id":             mlb_id,
            "pa":                 len(df),
            "avg_exit_velocity":  _safe_mean(df, "launch_speed"),
            "barrel_rate":        _barrel_rate(df),
            "xba":                _safe_mean(df, "estimated_ba_using_speedangle", decimals=3),
            "whiff_rate":         _calc_whiff_rate(df),
            "walk_rate":          _calc_walk_rate(df),
            "ba":                 _calc_ba(df),
        }

    def get_breakout_signals(self, roster: list[dict]) -> list[dict]:
        """
        Given a roster list from YahooClient.get_my_roster(),
        returns hitters with positive Statcast signals ordered by score.

        Looks up MLB ID from combined_players.json via player name.
        Skips players with no MLB ID or insufficient PA.
        """
        signals = []

        for player in roster:
            # Skip pitchers
            pos = player.get("primary_position", "")
            eligible = player.get("eligible_positions") or []
            if pos in ("SP", "RP", "P") or "SP" in eligible:
                continue

            name   = player.get("name", "")
            mlb_id = self.get_mlb_id(name)

            if not mlb_id:
                continue

            metrics = self.get_hitter_metrics(name, mlb_id=mlb_id)
            if not metrics or metrics.get("pa", 0) < 15:
                continue

            score = _compute_signal_score(metrics)
            if score > 0:
                signals.append({**metrics, "signal_score": round(score, 1)})

        return sorted(signals, key=lambda x: x["signal_score"], reverse=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_mean(df: pd.DataFrame, col: str, decimals: int = 1) -> float | None:
    if col not in df.columns:
        return None
    series = df[col].dropna()
    if series.empty:
        return None
    return round(float(series.mean()), decimals)


def _barrel_rate(df: pd.DataFrame) -> float | None:
    if "barrel" not in df.columns:
        return None
    total = len(df)
    if total == 0:
        return None
    return round((df["barrel"].sum() / total) * 100, 1)


def _calc_whiff_rate(df: pd.DataFrame) -> float | None:
    if "description" not in df.columns:
        return None
    swings = df[df["description"].isin([
        "swinging_strike", "foul", "hit_into_play", "foul_tip"
    ])]
    whiffs = df[df["description"] == "swinging_strike"]
    if len(swings) == 0:
        return None
    return round(len(whiffs) / len(swings) * 100, 1)


def _calc_walk_rate(df: pd.DataFrame) -> float | None:
    if "events" not in df.columns:
        return None
    pa    = df[df["events"].notna()]
    walks = pa[pa["events"] == "walk"]
    if len(pa) == 0:
        return None
    return round(len(walks) / len(pa) * 100, 1)


def _calc_ba(df: pd.DataFrame) -> float | None:
    if "events" not in df.columns:
        return None
    ab = df[df["events"].isin([
        "single", "double", "triple", "home_run",
        "field_out", "strikeout", "grounded_into_double_play",
        "force_out", "double_play", "field_error",
        "fielders_choice", "fielders_choice_out",
        "strikeout_double_play", "triple_play",
    ])]
    hits = ab[ab["events"].isin(["single", "double", "triple", "home_run"])]
    if len(ab) == 0:
        return None
    return round(len(hits) / len(ab), 3)


def _compute_signal_score(metrics: dict) -> float:
    """
    Positive composite score for breakout potential.
    Higher = stronger bullish signal.
    """
    score = 0.0

    barrel = metrics.get("barrel_rate")
    ev     = metrics.get("avg_exit_velocity")
    walk   = metrics.get("walk_rate")
    whiff  = metrics.get("whiff_rate")
    xba    = metrics.get("xba")
    ba     = metrics.get("ba")

    if barrel and barrel > 10:
        score += 2.0
    if ev and ev > 92:
        score += 1.5
    if walk and walk > 10:
        score += 1.0
    if whiff and whiff < 20:
        score += 1.0
    if xba and ba and (xba - ba) > 0.030:
        score += 2.0  # xBA well above BA = due for positive regression

    return score