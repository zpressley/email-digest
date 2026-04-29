"""
weekly_matchup_projection.py
FBP Email Digest — Pitcher projection layer.

Pure projection mechanics, lifted from weekly_matchup_engine.py:
  - Pitcher appearance log fetch (with disk cache for historical seasons)
  - Recency-weighted bootstrap resampling
  - SP/RP role classification
  - RP availability model
  - Bad/avg/good scenario builder shifted by opponent offense rank

No knowledge of categories, decisions, rendering, or league rules \u2014 those
live in weekly_matchup_evaluator and weekly_matchup_renderer.
"""

import json
import math
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEASONS          = [2024, 2025, 2026]
SEASON_MULT      = {2024: 0.5, 2025: 1.0, 2026: 1.0}  # 2026 = no discount, max recency
DECAY_HALFLIFE   = 45
TODAY            = date.today()
CURRENT_SEASON   = 2026

# Disk cache for historical pitcher logs
PITCHER_LOG_CACHE_DIR = "data/pitcher_logs"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AppearanceLog:
    ip:     float
    k:      int
    er:     int
    hr:     int
    h:      int
    bb:     int
    tb:     float
    qs:     int
    date:   str
    season: int
    weight: float


@dataclass
class AppOutcome:
    label: str
    ip:    float
    k:     int
    er:    int
    hr:    int
    h:     int
    bb:    int
    tb:    float
    qs:    int
    app:   int
    era:   float
    k9:    float
    h9:    float
    bb9:   float


@dataclass
class PitcherProjection:
    name:             str
    team:             str
    opponent:         str
    opp_offense_rank: int
    good:             AppOutcome
    avg:              AppOutcome
    bad:              AppOutcome
    log_count:        int
    data_quality:     str
    is_rp:            bool
    expected_apps:    float
    game_date:        str  = ""   # first start date this week (YYYY-MM-DD)
    last_3:           list = field(default_factory=list)  # 3 most recent SP appearances


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_ip(ip_str: str) -> float:
    """Convert '6.2' (6 full innings + 2 outs) → 6.667 decimal."""
    try:
        parts = str(ip_str).split(".")
        return int(parts[0]) + (int(parts[1]) if len(parts) > 1 else 0) / 3
    except (ValueError, IndexError):
        return 0.0


def _fmt_ip(ip: float) -> str:
    """Format decimal IP back to baseball notation: 5.333 → '5.1', 6.667 → '6.2'."""
    full = int(ip)
    frac = ip - full
    if frac < 0.17:
        thirds = 0
    elif frac < 0.5:
        thirds = 1
    else:
        thirds = 2
    return f"{full}.{thirds}"


def _recency_weight(date_str: str, season: int) -> float:
    """
    weight = season_mult × exp(−days_ago / DECAY_HALFLIFE)
    Unknown dates treated as 180 days old.
    """
    try:
        app_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        days_ago = max(0, (TODAY - app_date).days)
    except (ValueError, TypeError):
        days_ago = 180
    decay = math.exp(-days_ago / DECAY_HALFLIFE)
    return SEASON_MULT.get(season, 0.5) * decay


def _offense_rank_adj(rank: int) -> int:
    """Percentile band shift based on opponent offense rank (1 = best offense)."""
    if rank <= 5:   return -9
    if rank <= 10:  return -5
    if rank >= 26:  return  9
    if rank >= 21:  return  5
    return 0


# ---------------------------------------------------------------------------
# Pitcher log disk cache
# Historical seasons are cached after first fetch to avoid redundant API calls.
# Current season (2026) is always fetched fresh.
# ---------------------------------------------------------------------------

def _cache_path(mlb_id: int, season: int) -> str:
    return os.path.join(PITCHER_LOG_CACHE_DIR, str(season), f"{mlb_id}.json")


def _load_log_from_cache(mlb_id: int, season: int) -> Optional[list]:
    """Returns cached splits list for a historical season, or None if not cached."""
    if season >= CURRENT_SEASON:
        return None  # never cache current season
    path = _cache_path(mlb_id, season)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _save_log_to_cache(mlb_id: int, season: int, splits: list):
    """Write splits to disk for a historical season."""
    if season >= CURRENT_SEASON:
        return  # never cache current season
    path = _cache_path(mlb_id, season)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(splits, f)


# ---------------------------------------------------------------------------
# TB rate fetch (season stats endpoint)
# ---------------------------------------------------------------------------

def fetch_tb_rate_and_role(mlb_id: int, mlb_client) -> dict:
    """
    Single season-stats pass returns both tb_rate and SP/RP role.
    Avoids the duplicate API call that fetch_tb_rate() + get_pitcher_role() would require.

    Role thresholds (starts / games):
      >= 0.70 → SP    |  <= 0.20 → RP    |  in-between → SP/RP
    Requires APP >= 3 for role; IP >= 5 + TB > 0 for tb_rate.
    Falls back to 1.45 (MLB avg) / 'RP' (conservative) if no data.
    """
    tb_rate = None
    role    = None

    for season in reversed(SEASONS):  # 2026 first, then 2025, 2024
        stats = mlb_client.get_pitcher_season_stats(mlb_id, season)
        if not stats:
            continue

        ip     = stats.get("IP",  0)
        tb     = stats.get("TB",  0)
        games  = stats.get("APP", 0)
        starts = stats.get("GS",  0)

        if tb_rate is None and ip >= 5 and tb > 0:
            tb_rate = round(tb / ip, 4)

        if role is None and games >= 3:
            start_pct = starts / games
            role = "SP" if start_pct >= 0.70 else (
                   "RP" if start_pct <= 0.20 else "SP/RP")

        if tb_rate is not None and role is not None:
            break   # both resolved — no need to check older seasons

    return {
        "tb_rate": tb_rate if tb_rate is not None else 1.45,
        "role":    role    if role    is not None else "RP",
    }


# ---------------------------------------------------------------------------
# RP availability model
# ---------------------------------------------------------------------------

def fetch_rp_availability(mlb_id: int, remaining_games: int,
                           mlb_client) -> float:
    """
    Expected appearances this week for a reliever.
    Workload rate = season apps / estimated team games.
    Recent usage penalty applied for appearances in last 4 days.
    """
    apps_per_game = 0.25

    for season in reversed(SEASONS):
        stats = mlb_client.get_pitcher_season_stats(mlb_id, season)
        if stats and stats.get("APP", 0) >= 5:
            days_into  = max(1, (TODAY - date(season, 3, 20)).days)
            team_games = min(162, int(days_into * 162 / 185))
            if team_games > 0:
                apps_per_game = stats["APP"] / team_games
            break

    recent_penalty = 0.0
    splits = mlb_client.get_pitcher_game_log(mlb_id, SEASONS[-1])
    for split in splits:
        date_str = split.get("date", "")
        try:
            game_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            days_ago  = (TODAY - game_date).days
            if days_ago <= 2:
                recent_penalty += 0.8
            elif days_ago <= 4:
                recent_penalty += 0.3
        except (ValueError, TypeError):
            pass

    return max(0.0, round(apps_per_game * remaining_games - recent_penalty, 2))


# ---------------------------------------------------------------------------
# Appearance log builder with disk cache
# ---------------------------------------------------------------------------

def fetch_appearance_logs(mlb_id: int, tb_rate: float,
                           mlb_client) -> list:
    """
    All pitching appearances from 2024, 2025, and 2026 with recency weights.
    Historical seasons loaded from disk cache if available;
    fetched from API and cached on first access.
    Current season (2026) always fetched fresh.
    No SP/RP filter — all appearances with IP > 0 included.
    """
    logs = []
    for season in SEASONS:
        # Load from cache for historical seasons
        splits = _load_log_from_cache(mlb_id, season)
        if splits is None:
            splits = mlb_client.get_pitcher_game_log(mlb_id, season)
            _save_log_to_cache(mlb_id, season, splits)

        for split in splits:
            stat     = split.get("stat", {})
            date_str = split.get("date", "")
            ip       = _parse_ip(str(stat.get("inningsPitched", "0.0")))
            if ip <= 0:
                continue
            k  = int(stat.get("strikeOuts",  0))
            er = int(stat.get("earnedRuns",   0))
            hr = int(stat.get("homeRuns",     0))
            h  = int(stat.get("hits",         0))
            bb = int(stat.get("baseOnBalls",  0))
            tb = round(tb_rate * ip, 2)
            qs = 1 if (ip >= 6.0 and er <= 3) else 0
            wt = _recency_weight(date_str, season)
            logs.append(AppearanceLog(
                ip=ip, k=k, er=er, hr=hr, h=h, bb=bb,
                tb=tb, qs=qs, date=date_str, season=season, weight=wt,
            ))
    return logs


# ---------------------------------------------------------------------------
# Recent starts helper
# ---------------------------------------------------------------------------

def get_last_n_starts(logs: list, n: int = 3) -> list:
    """
    Returns the n most recent appearances sorted newest-first.
    NO minimum IP filter — a 1-inning blow-up is critical context and
    must NOT be silently dropped.
    """
    return sorted(logs, key=lambda lg: lg.date, reverse=True)[:n]


# ---------------------------------------------------------------------------
# Weighted bootstrap projection builder
# ---------------------------------------------------------------------------

def _to_outcome(log: AppearanceLog, label: str,
                app_count: int = 1) -> AppOutcome:
    ip = max(0.01, log.ip * app_count)
    k  = log.k  * app_count
    er = log.er * app_count
    hr = log.hr * app_count
    h  = log.h  * app_count
    bb = log.bb * app_count
    tb = log.tb * app_count
    qs = log.qs * app_count
    return AppOutcome(
        label=label, ip=ip, k=k, er=er, hr=hr, h=h, bb=bb,
        tb=round(tb, 1), qs=qs, app=app_count,
        era=round(er * 9 / ip, 2),
        k9= round(k  * 9 / ip, 2),
        h9= round(h  * 9 / ip, 2),
        bb9=round(bb * 9 / ip, 2),
    )


def build_projection(name: str, team: str, opponent: str, opp_rank: int,
                     logs: list, is_rp: bool,
                     expected_apps: float = 1.0) -> PitcherProjection:
    """
    Weighted bootstrap resampling → correlated bad/avg/good scenarios.
    Each resample draws one complete historical appearance.
    Bands shifted by opponent offense quality.
    RPs: outcomes scaled by round(expected_apps).
    """
    if not logs or len(logs) < 4:
        return _fallback_projection(name, team, opponent, opp_rank,
                                     is_rp, expected_apps)

    dq = ("STRONG" if len(logs) >= 20 else
          "OK"     if len(logs) >= 8  else "THIN")

    N       = 2000
    adj     = _offense_rank_adj(opp_rank)
    total_w = sum(lg.weight for lg in logs)
    probs   = [lg.weight / total_w for lg in logs]

    resamples = random.choices(logs, weights=probs, k=N)

    def quality(lg: AppearanceLog) -> float:
        return lg.ip * 1.5 + lg.k * 0.4 - lg.er * 1.2 - lg.hr * 0.8

    scored = sorted(resamples, key=quality)

    def band_pick(lo_pct: int, hi_pct: int) -> AppearanceLog:
        lo = max(0,   min(95,  lo_pct + adj))
        hi = max(5,   min(100, hi_pct + adj))
        lo_i = int(lo / 100 * N)
        hi_i = int(hi / 100 * N)
        band = scored[lo_i:hi_i] or scored
        return band[len(band) // 2]

    app_count = max(1, round(expected_apps)) if is_rp else 1

    return PitcherProjection(
        name=name, team=team, opponent=opponent, opp_offense_rank=opp_rank,
        bad =_to_outcome(band_pick(0,  20),  "bad",     app_count),
        avg =_to_outcome(band_pick(40, 60),  "average", app_count),
        good=_to_outcome(band_pick(80, 100), "good",    app_count),
        log_count=len(logs),
        data_quality=dq,
        is_rp=is_rp,
        expected_apps=expected_apps if is_rp else 1.0,
    )


def _fallback_projection(name, team, opponent, opp_rank,
                          is_rp, expected_apps) -> PitcherProjection:
    adj  = _offense_rank_adj(opp_rank)
    apps = max(1, round(expected_apps)) if is_rp else 1

    if is_rp:
        good = AppOutcome("good",    1.0*apps, 1*apps, 0,       0, 1*apps, 0,
                           round(1.45*apps,1), 0, apps,
                           0.0, 9.0, 6.0, 2.7)
        avg  = AppOutcome("average", 0.7*apps, 1*apps, 1*apps,  0, 1*apps, 1,
                           round(1.45*0.7*apps,1), 0, apps,
                           round(9/max(0.01,0.7*apps),2),
                           round(9/max(0.01,0.7*apps),2),
                           round(9/max(0.01,0.7*apps),2),
                           round(9/max(0.01,0.7*apps),2))
        bad  = AppOutcome("bad",     0.2*apps, 0,      2*apps,  1, 3*apps, 1,
                           round(4.0*apps,1), 0, apps, 99.0, 0.0, 99.0, 99.0)
    else:
        ip_g = round(max(4.5, 6.5 + adj * 0.05), 1)
        ip_b = round(max(1.5, 3.0 + adj * 0.05), 1)
        good = AppOutcome("good",    ip_g, 8, 1, 0, 5, 1,
                           round(1.45*ip_g,1), 1, 1,
                           round(9/ip_g,2), round(72/ip_g,2),
                           round(45/ip_g,2), round(9/ip_g,2))
        avg  = AppOutcome("average", 5.1,  6, 3, 1, 7, 2,
                           round(1.45*5.1,1), 0, 1,
                           5.29, 10.6, 12.4, 3.5)
        bad  = AppOutcome("bad",     ip_b, 3, 5, 2, 10, 3,
                           round(1.45*ip_b,1), 0, 1,
                           round(45/ip_b,2), round(27/ip_b,2),
                           round(90/ip_b,2), round(27/ip_b,2))

    return PitcherProjection(
        name=name, team=team, opponent=opponent, opp_offense_rank=opp_rank,
        good=good, avg=avg, bad=bad, log_count=0,
        data_quality="FALLBACK", is_rp=is_rp,
        expected_apps=expected_apps if is_rp else 1.0,
    )


# ---------------------------------------------------------------------------
# Full pitcher projection
# ---------------------------------------------------------------------------

def project_pitcher(pitcher_info: dict, mlb_id: int,
                     remaining_games: int,
                     mlb_client) -> Optional[PitcherProjection]:
    """Fetch logs, classify SP/RP via MLB API (not Yahoo eligibility), build projection."""
    name     = pitcher_info["name"]
    team     = pitcher_info.get("team", "")
    opponent = pitcher_info.get("opponent", "UNK")
    opp_rank = pitcher_info.get("opp_rank", 15)
    position = pitcher_info.get("position", "")

    # Yahoo position as initial guess (may be eligibility-based, not role-based)
    is_rp = ("SP" not in position and
              any(x in position for x in ("RP", "MR", "CL", "P")))

    # Single API call: tb_rate + MLB role (starts/games ratio from season stats)
    meta     = fetch_tb_rate_and_role(mlb_id, mlb_client)
    tb_rate  = meta["tb_rate"]
    mlb_role = meta["role"]

    logs = fetch_appearance_logs(mlb_id, tb_rate, mlb_client)

    # Override Yahoo guess with MLB API role — definitive for clear SP/RP cases
    if mlb_role == "SP":
        is_rp = False
    elif mlb_role == "RP":
        is_rp = True
    else:  # SP/RP — use median IP from logs as tiebreaker
        if logs:
            sorted_ips = sorted(lg.ip for lg in logs)
            median_ip  = sorted_ips[len(sorted_ips) // 2]
            is_rp = median_ip < 3.0

    expected_apps = 1.0
    if is_rp:
        expected_apps = fetch_rp_availability(mlb_id, remaining_games, mlb_client)
        if expected_apps < 0.3:
            return None

    proj = build_projection(name, team, opponent, opp_rank,
                             logs, is_rp, expected_apps)
    if not proj.is_rp:
        proj.last_3 = get_last_n_starts(logs)
    return proj
