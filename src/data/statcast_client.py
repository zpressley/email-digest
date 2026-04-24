"""
Statcast data client using pybaseball.

Metric selection and sample size gates are based on Russell Carleton's
split-half reliability research and Pitcher List's Statcast stabilization work.

Stabilization thresholds used:
    Plate discipline (pitch-level, accumulate on every pitch):
        Whiff rate (SwStr%)  — ~60-80 PA to stabilize
        Chase rate (O-Swing%) — ~50-80 PA, one of the most stable early metrics
        K%                   — ~60 PA

    Batted ball / contact quality (only on balls in play):
        Barrel rate (Brls/BBE%) — ~50 BBE (~18 games) per Carleton/Pitcher List
        Hard hit rate           — ~50 BBE
        EV on FB/LD             — ~50 BBE, stickiest year-to-year metric

    Expected stats (aggregate contact quality + outcomes):
        xBA, xSLG, xwOBA        — 200+ PA needed, not shown early season

    NOT USED:
        Average EV (standalone) — 0.36 correlation with HR, weakest signal,
                                   barrel rate already captures best EV outcomes
        BABIP                   — 1000+ PA / 3 seasons to stabilize, pure noise

Metric priority order:
    1. Whiff rate + Chase rate (fastest to stabilize, every pitch counts)
    2. Barrel rate (best power predictor at 0.73 HR correlation, needs 50 BBE)
    3. Hard hit rate (secondary power signal, same 50 BBE gate)
    4. xBA only shown after 200 PA

Early season behavior:
    - Fewer than MIN_PA_DISCIPLINE: show nothing
    - MIN_PA_DISCIPLINE met but fewer than MIN_BBE_CONTACT BBE:
      show whiff/chase only, mark barrel/hard hit as insufficient
    - All thresholds met: show full profile
"""
import pandas as pd
import json
import os
from datetime import date, timedelta

try:
    import pybaseball
    from pybaseball import statcast_batter
    PYBASEBALL_AVAILABLE = True
except ImportError:
    PYBASEBALL_AVAILABLE = False

# ── Sample size gates (research-backed) ─────────────────────────────────────
MIN_PA_DISCIPLINE   = 80    # whiff rate, chase rate — pitch-level metrics
MIN_BBE_CONTACT     = 50    # barrel rate, hard hit rate — batted ball events
MIN_PA_EXPECTED     = 200   # xBA, xSLG — don't show before this

STATCAST_CACHE_FILE = os.getenv(
    "COMBINED_PLAYERS_PATH",
    "data/combined_players.json"
)

# Rolling window for current-season data
STATCAST_DAYS = int(os.getenv("STATCAST_ROLLING_DAYS", "30"))

# Daily snapshot cache: yesterday's metrics ↔ today's metrics → delta.
# Early season, the "prior window" approach (fetching days 31-60 ago) returns
# empty data because it falls inside spring training. Snapshot cache sidesteps
# that by comparing today's metrics to what we computed N days ago.
STATCAST_SNAPSHOT_DIR = os.getenv(
    "STATCAST_SNAPSHOT_DIR", "data/statcast_snapshots"
)
STATCAST_DELTA_DAYS = int(os.getenv("STATCAST_DELTA_DAYS", "7"))
STATCAST_DELTA_MIN  = int(os.getenv("STATCAST_DELTA_MIN", "3"))
STATCAST_DELTA_MAX  = int(os.getenv("STATCAST_DELTA_MAX", "14"))


def _snapshot_path(d: date) -> str:
    return os.path.join(STATCAST_SNAPSHOT_DIR, f"{d.strftime('%Y-%m-%d')}.json")


def save_statcast_snapshot(snapshot: dict, d: date | None = None) -> None:
    """
    Write today's per-player metrics to data/statcast_snapshots/YYYY-MM-DD.json.
    snapshot: {mlb_id (str or int): {whiff_rate, chase_rate, barrel_rate, hard_hit_rate, ...}}
    """
    if not snapshot:
        return
    d = d or date.today()
    try:
        os.makedirs(STATCAST_SNAPSHOT_DIR, exist_ok=True)
        path = _snapshot_path(d)
        normalized = {str(k): v for k, v in snapshot.items()}
        with open(path, "w") as f:
            json.dump(normalized, f)
        print(f"  💾 Statcast snapshot: wrote {len(normalized)} players to {path}")
    except Exception as e:
        print(f"  ⚠️  Statcast snapshot write error: {e}")


_comparison_snapshot_cache: dict | None = None


def _load_comparison_snapshot() -> dict:
    """
    Walk backward from STATCAST_DELTA_MIN..STATCAST_DELTA_MAX days ago,
    preferring the one closest to STATCAST_DELTA_DAYS. Returns {} if none found.
    Memoized for the life of the process so per-player calls don't hit disk.
    """
    global _comparison_snapshot_cache
    if _comparison_snapshot_cache is not None:
        return _comparison_snapshot_cache
    today = date.today()
    candidates = []
    for n in range(STATCAST_DELTA_MIN, STATCAST_DELTA_MAX + 1):
        p = _snapshot_path(today - timedelta(days=n))
        if os.path.exists(p):
            candidates.append((abs(n - STATCAST_DELTA_DAYS), n, p))
    if not candidates:
        print("  📊 Statcast deltas: no prior snapshot yet — deltas skipped today, will start tomorrow")
        _comparison_snapshot_cache = {}
        return _comparison_snapshot_cache
    candidates.sort()
    _, best_n, best_path = candidates[0]
    try:
        with open(best_path) as f:
            data = json.load(f)
        print(f"  📊 Statcast deltas: comparing to snapshot from {best_n} days ago")
        _comparison_snapshot_cache = {str(k): v for k, v in data.items()}
    except Exception as e:
        print(f"  ⚠️  Statcast snapshot read error ({best_path}): {e}")
        _comparison_snapshot_cache = {}
    return _comparison_snapshot_cache


class StatcastClient:
    def __init__(self):
        self._id_cache = self._load_id_cache()

    def _load_id_cache(self) -> dict[str, int]:
        """
        Load MLB ID → name mapping from combined_players.json.
        combined_players.json is the single source of truth for player IDs.
        Never use pybaseball playerid_lookup — returns empty.
        """
        cache = {}
        try:
            if not os.path.exists(STATCAST_CACHE_FILE):
                return cache
            with open(STATCAST_CACHE_FILE) as f:
                players = json.load(f)
            for p in players:
                mlb_id = p.get("mlb_id")
                name   = p.get("name", "").strip()
                if mlb_id and name:
                    cache[name.lower()] = int(mlb_id)
            print(f"  📊 Statcast ID cache: {len(cache)} players loaded")
        except Exception as e:
            print(f"  ⚠️  Statcast cache load error: {e}")
        return cache

    def get_mlb_id(self, player_name: str) -> int | None:
        """Look up MLB ID by player name. Case-insensitive."""
        name_lower = player_name.lower().strip()

        # Exact match
        if name_lower in self._id_cache:
            return self._id_cache[name_lower]

        # Last name match
        last_name = name_lower.split()[-1] if name_lower else ""
        for cached_name, mlb_id in self._id_cache.items():
            if last_name and cached_name.endswith(last_name):
                return mlb_id

        return None

    def get_hitter_metrics(
        self,
        player_name: str,
        mlb_id: int | None = None,
        days: int = STATCAST_DAYS,
    ) -> dict | None:
        """
        Fetch Statcast metrics for a hitter over the last N days.

        Returns a dict with metrics and sample size flags, or None if
        the player has insufficient data to show anything meaningful.

        Keys:
            name               — player name
            pa                 — plate appearances in window
            bbe                — batted ball events in window
            whiff_rate         — % swings that miss (reliable at 80 PA)
            chase_rate         — % swings at pitches outside zone (reliable at 80 PA)
            barrel_rate        — Brls/BBE% (reliable at 50 BBE)
            hard_hit_rate      — % BBE at 95+ mph exit velocity (reliable at 50 BBE)
            xba                — expected batting average (shown only at 200+ PA)
            has_discipline     — True if PA >= MIN_PA_DISCIPLINE
            has_contact        — True if BBE >= MIN_BBE_CONTACT
            has_expected       — True if PA >= MIN_PA_EXPECTED
            insufficient_data  — True if below all thresholds
        """
        if not PYBASEBALL_AVAILABLE:
            return None

        if not mlb_id:
            mlb_id = self.get_mlb_id(player_name)
        if not mlb_id:
            return None

        end_date   = date.today()
        start_date = end_date - timedelta(days=days)

        try:
            df = statcast_batter(
                start_dt=start_date.strftime("%Y-%m-%d"),
                end_dt=end_date.strftime("%Y-%m-%d"),
                player_id=mlb_id,
            )
        except Exception as e:
            print(f"  ⚠️  Statcast fetch error for {player_name}: {e}")
            return None

        if df is None or df.empty:
            return None

        pa  = _count_pa(df)
        bbe = _count_bbe(df)

        # If below even the lowest threshold, return None — show nothing
        if pa < MIN_PA_DISCIPLINE:
            return None

        has_discipline = pa  >= MIN_PA_DISCIPLINE
        has_contact    = bbe >= MIN_BBE_CONTACT
        has_expected   = pa  >= MIN_PA_EXPECTED

        # Plate discipline metrics — always compute if PA threshold met
        whiff_rate = _calc_whiff_rate(df) if has_discipline else None
        chase_rate = _calc_chase_rate(df) if has_discipline else None

        # Contact quality metrics — only if BBE threshold met
        barrel_rate    = _calc_barrel_rate(df)  if has_contact else None
        hard_hit_rate  = _calc_hard_hit_rate(df) if has_contact else None

        # Expected stats — only if PA threshold met
        xba  = _calc_xba(df)  if has_expected else None

        # Deltas come from yesterday's (or ~7-day-old) snapshot instead of a
        # fresh "prior window" fetch. The old approach tried to read days 31-60
        # ago, which falls inside spring training in April — Statcast returns
        # empty and deltas never render. Snapshot comparison is the only way
        # to show meaningful early-season movement.
        deltas = {}
        prior_snap = _load_comparison_snapshot().get(str(mlb_id))
        if prior_snap:
            pairs = [
                ("whiff_rate",    whiff_rate,    prior_snap.get("whiff_rate")),
                ("chase_rate",    chase_rate,    prior_snap.get("chase_rate")),
                ("barrel_rate",   barrel_rate,   prior_snap.get("barrel_rate")),
                ("hard_hit_rate", hard_hit_rate, prior_snap.get("hard_hit_rate")),
            ]
            for key, curr_val, prior_val in pairs:
                if curr_val is not None and prior_val is not None:
                    deltas[key] = {
                        "current": round(curr_val,  1),
                        "prior":   round(prior_val, 1),
                        "delta":   round(curr_val - prior_val, 1),
                    }

        return {
            "name":           player_name,
            "mlb_id":         mlb_id,
            "pa":             pa,
            "bbe":            bbe,
            "whiff_rate":     whiff_rate,
            "chase_rate":     chase_rate,
            "barrel_rate":    barrel_rate,
            "hard_hit_rate":  hard_hit_rate,
            "xba":            xba,
            "deltas":         deltas,
            "has_discipline": has_discipline,
            "has_contact":    has_contact,
            "has_expected":   has_expected,
            "insufficient_data": not has_discipline,
        }


# ── Metric calculation helpers ────────────────────────────────────────────────

def _count_pa(df: pd.DataFrame) -> int:
    """Count plate appearances — rows where a PA-ending event occurred."""
    pa_events = {
        "strikeout", "walk", "hit_by_pitch", "single", "double",
        "triple", "home_run", "field_out", "grounded_into_double_play",
        "force_out", "field_error", "fielders_choice",
        "fielders_choice_out", "double_play", "triple_play",
        "sac_fly", "sac_bunt", "intent_walk",
    }
    if "events" not in df.columns:
        return len(df[df["type"] == "X"]) if "type" in df.columns else 0
    return int(df["events"].dropna().isin(pa_events).sum())


def _count_bbe(df: pd.DataFrame) -> int:
    """Count batted ball events — balls actually put in play."""
    if "type" not in df.columns:
        return 0
    return int((df["type"] == "X").sum())


def _calc_whiff_rate(df: pd.DataFrame) -> float | None:
    """
    Whiff rate = swinging strikes / total swings.
    Stabilizes ~60-80 PA. One of the fastest metrics to become reliable.
    """
    if "description" not in df.columns:
        return None

    swing_descs = {
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "hit_into_play",
        "hit_into_play_no_out", "hit_into_play_score",
        "missed_bunt", "foul_bunt",
    }
    miss_descs = {"swinging_strike", "swinging_strike_blocked"}

    swings = df[df["description"].isin(swing_descs)]
    misses = df[df["description"].isin(miss_descs)]

    if len(swings) == 0:
        return None

    return round(len(misses) / len(swings) * 100, 1)


def _calc_chase_rate(df: pd.DataFrame) -> float | None:
    """
    Chase rate (O-Swing%) = swings at pitches outside the strike zone /
    total pitches outside the strike zone.

    Pitcher List: 'One of the most stable offensive statistics —
    stabilizes quickly because it's measured on every pitch outside the zone.'
    Gate: MIN_PA_DISCIPLINE (80 PA).

    Statcast zone codes:
        1-9  = in the strike zone
        11-14 = outside the strike zone
    """
    if "zone" not in df.columns or "description" not in df.columns:
        return None

    swing_descs = {
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "hit_into_play",
        "hit_into_play_no_out", "hit_into_play_score",
        "missed_bunt", "foul_bunt",
    }

    outside = df[df["zone"].isin([11, 12, 13, 14])]
    if len(outside) == 0:
        return None

    chases = outside[outside["description"].isin(swing_descs)]
    return round(len(chases) / len(outside) * 100, 1)


def _calc_barrel_rate(df: pd.DataFrame) -> float | None:
    """
    Barrel rate (Brls/BBE%) = barrels / batted ball events.

    Use Brls/BBE% not Brls/PA% — BBE% is more predictive year-to-year
    because it isn't affected by changes in K/BB rates.
    (Pitcher List research: Brls/BBE% vs Brls/PA% stickiness comparison)

    Gate: MIN_BBE_CONTACT (50 BBE, ~18 games).
    Correlation with HR: 0.73 (HighLevBaseball, 2025 data).
    """
    if "launch_speed" not in df.columns or "launch_angle" not in df.columns:
        return None

    bbe = df[df["type"] == "X"].copy() if "type" in df.columns else df.copy()
    if len(bbe) == 0:
        return None

    # Barrel definition: EV >= 98 mph with launch angle in optimal range
    # The range expands as EV increases above 98 mph
    def is_barrel(row) -> bool:
        try:
            ev = float(row["launch_speed"])
            la = float(row["launch_angle"])
        except (ValueError, TypeError):
            return False
        if ev < 98:
            return False
        if ev < 99:
            return 26 <= la <= 30
        if ev < 100:
            return 25 <= la <= 31
        if ev < 101:
            return 24 <= la <= 33
        if ev < 102:
            return 23 <= la <= 34
        if ev < 103:
            return 22 <= la <= 35
        if ev < 104:
            return 21 <= la <= 36
        if ev < 105:
            return 20 <= la <= 37
        return 17 <= la <= 40

    barrels = bbe.apply(is_barrel, axis=1).sum()
    return round(barrels / len(bbe) * 100, 1)


def _calc_hard_hit_rate(df: pd.DataFrame) -> float | None:
    """
    Hard hit rate = BBE with exit velocity >= 95 mph / total BBE.
    Secondary power signal. Correlation with HR: 0.39.
    Gate: MIN_BBE_CONTACT (50 BBE).
    """
    if "launch_speed" not in df.columns:
        return None

    bbe = df[df["type"] == "X"].copy() if "type" in df.columns else df.copy()
    if len(bbe) == 0:
        return None

    hard_hit = bbe[bbe["launch_speed"] >= 95]
    return round(len(hard_hit) / len(bbe) * 100, 1)


def _calc_xba(df: pd.DataFrame) -> float | None:
    """
    Expected batting average — mean of estimated_ba_using_speedangle.
    Only shown at 200+ PA per MIN_PA_EXPECTED gate.
    """
    col = "estimated_ba_using_speedangle"
    if col not in df.columns:
        return None

    valid = df[col].dropna()
    if len(valid) == 0:
        return None

    return round(float(valid.mean()), 3)