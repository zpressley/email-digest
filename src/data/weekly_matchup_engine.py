"""
weekly_matchup_engine.py
FBP Email Digest — Weekly Matchup Projection Engine (2026)

Projection methodology:
  - Pitcher appearance logs: 2024 + 2025 via MLB Stats API game log endpoint
  - Historical seasons (< 2026) cached to data/pitcher_logs/{season}/{mlb_id}.json
    after first fetch — avoids redundant API calls on every digest run
  - 2026 (current season) always fetched fresh
  - Recency decay: weight = season_mult × exp(−days_ago / 45)
      2024 season_mult = 0.5, 2025 season_mult = 1.0
  - Weighted bootstrap resampling (N=2000): draws ONE complete appearance
    preserving natural stat correlations (IP/K/ER all from same game)
  - Bands shifted by opponent offense rank: top-5 → −9 pct pts, bottom-5 → +9
  - TB allowed: derived from season TB/IP rate (not available in game log)
  - RP availability: workload rate × remaining games − recent usage penalty

Stat key naming (avoids Yahoo ID collisions):
  K       = pitching strikeouts
  K_hit   = batting strikeouts
  HR      = pitching HR allowed
  HR_hit  = batting home runs
  ERA, K/9, H/9, BB/9 derived from components — not fetched directly

Categories (20):
  Hitting higher-better: R, H, HR_hit, RBI, SB, BB, TB, AVG, OPS
  Hitting lower-better:  K_hit
  Pitching higher-better: APP, K, K/9, QS
  Pitching lower-better:  ER, HR, TB, ERA, H/9, BB/9

Entry point:
  get_weekly_matchup_section(yahoo_client, mlb_client,
                              team_offense_ranker, combined_players)
"""

import json
import math
import os
import random
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEASONS          = [2024, 2025, 2026]
SEASON_MULT      = {2024: 0.5, 2025: 1.0, 2026: 1.0}  # 2026 = no discount, max recency
DECAY_HALFLIFE   = 45
IP_MINIMUM       = 35.0
TODAY            = date.today()
CURRENT_SEASON   = 2026

# Disk cache for historical pitcher logs
PITCHER_LOG_CACHE_DIR = "data/pitcher_logs"

# Category metadata
# Hitting key names use _hit suffix where collision risk exists
HITTING_CATS = {
    "R":      {"higher_better": True,  "rate": False},
    "H":      {"higher_better": True,  "rate": False},
    "HR_hit": {"higher_better": True,  "rate": False},
    "RBI":    {"higher_better": True,  "rate": False},
    "SB":     {"higher_better": True,  "rate": False},
    "BB":     {"higher_better": True,  "rate": False},
    "K_hit":  {"higher_better": False, "rate": False},
    "TB":     {"higher_better": True,  "rate": False},
    "AVG":    {"higher_better": True,  "rate": True},
    "OPS":    {"higher_better": True,  "rate": True},
}
PITCHING_CATS = {
    "APP":  {"higher_better": True,  "rate": False},
    "ER":   {"higher_better": False, "rate": False},
    "HR":   {"higher_better": False, "rate": False},
    "K":    {"higher_better": True,  "rate": False},
    "TB":   {"higher_better": False, "rate": False},
    "ERA":  {"higher_better": False, "rate": True},
    "K/9":  {"higher_better": True,  "rate": True},
    "H/9":  {"higher_better": False, "rate": True},
    "BB/9": {"higher_better": False, "rate": True},
    "QS":   {"higher_better": True,  "rate": False},
}
ALL_CATS = {**HITTING_CATS, **PITCHING_CATS}


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


@dataclass
class TeamWeekLine:
    scenario: str
    hitting:  dict
    pitching: dict
    ip_total: float = 0.0

    def get(self, cat: str):
        v = self.hitting.get(cat)
        if v is None:
            v = self.pitching.get(cat, 0)
        return v if v is not None else 0


@dataclass
class CatOutcome:
    cat:               str
    your_avg_val:      float
    your_floor_val:    float
    opp_avg_val:       float
    opp_ceil_val:      float
    currently_winning: bool
    floor_beats_ceil:  bool
    avg_beats_avg:     bool
    action:            str
    note:              str


@dataclass
class StartDecision:
    name:             str
    team:             str
    opponent:         str
    opp_offense_rank: int
    recommendation:   str
    confidence:       str
    reasoning:        str
    ip_floor_flag:    bool
    projection:       PitcherProjection
    start_date_label: str   = ""   # "Today", "Tomorrow", "Thu Apr 17"
    opp_k_pct:        float = 0.0  # opposing team K% from team_offense_ranker


@dataclass
class StreamerRec:
    name:             str
    team:             str
    opponent:         str
    opp_offense_rank: int
    matchup_grade:    str
    projection:       PitcherProjection
    primary_value:    str
    reasoning:        str


@dataclass
class IPPlan:
    banked:           float
    roster_projected: float
    total_projected:  float
    shortfall:        float
    streamers_needed: int
    note:             str


@dataclass
class WeekPlan:
    your_floor:      TeamWeekLine
    your_avg:        TeamWeekLine
    opp_ceiling:     TeamWeekLine
    opp_avg:         TeamWeekLine
    cat_outcomes:    list
    start_decisions: list
    streamers:       list
    ip_plan:         IPPlan
    summary:         str
    opponent_name:     str = "OPP"
    current_score_you: int = 0
    current_score_opp: int = 0
    score_as_of:       str = ""
    bullpen_summary:   str = ""


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

def fetch_tb_rate(mlb_id: int, mlb_client) -> float:
    """
    TB allowed / IP from season stats.
    TB is in season-level stats but NOT in game log splits.
    Falls back to MLB average (1.45) if unavailable.
    """
    for season in reversed(SEASONS):  # tries 2026 first, falls back to 2025/2024
        stats = mlb_client.get_pitcher_season_stats(mlb_id, season)
        # 5 IP threshold (not 10) so early 2026 data isn't skipped
        if stats and stats.get("IP", 0) >= 5 and stats.get("TB", 0) > 0:
            return round(stats["TB"] / stats["IP"], 4)
    return 1.45


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
    """Fetch logs, infer SP/RP, build projection."""
    name     = pitcher_info["name"]
    team     = pitcher_info.get("team", "")
    opponent = pitcher_info.get("opponent", "UNK")
    opp_rank = pitcher_info.get("opp_rank", 15)
    position = pitcher_info.get("position", "")

    is_rp = ("SP" not in position and
              any(x in position for x in ("RP", "MR", "CL", "P")))

    tb_rate = fetch_tb_rate(mlb_id, mlb_client)
    logs    = fetch_appearance_logs(mlb_id, tb_rate, mlb_client)

    if logs:
        sorted_ips = sorted(lg.ip for lg in logs)
        median_ip  = sorted_ips[len(sorted_ips) // 2]
        if median_ip < 3.0:
            is_rp = True
        elif median_ip >= 4.0:
            is_rp = False

    expected_apps = 1.0
    if is_rp:
        expected_apps = fetch_rp_availability(mlb_id, remaining_games, mlb_client)
        if expected_apps < 0.3:
            return None

    return build_projection(name, team, opponent, opp_rank,
                             logs, is_rp, expected_apps)


# ---------------------------------------------------------------------------
# Pitching line aggregator
# Reads from banked dict using verified key names (K, HR, etc.)
# Derives rate stats from raw components — not fetched directly from Yahoo
# ---------------------------------------------------------------------------

def aggregate_pitching_line(projections: list,
                              scenario: str,
                              banked: dict) -> dict:
    """
    Sum pitcher outcomes into weekly pitching line.
    ERA, K/9, H/9, BB/9 derived from accumulated IP/ER/K/H_allowed/BB_allowed.
    """
    attr = {"good": "good", "avg": "avg", "bad": "bad",
            "average": "avg", "poor": "bad"}.get(scenario, "avg")

    ip   = float(banked.get("IP",         0.0))
    er   = float(banked.get("ER",         0))
    k    = float(banked.get("K",          0))   # pitching K
    hr   = float(banked.get("HR",         0))   # HR allowed
    tb   = float(banked.get("TB",         0))   # TB allowed (stubbed)
    app  = float(banked.get("APP",        0))
    qs   = float(banked.get("QS",         0))
    h_a  = float(banked.get("H_allowed",  0))
    bb_a = float(banked.get("BB_allowed", 0))

    for proj in projections:
        o    = getattr(proj, attr)
        ip  += o.ip;   er   += o.er;  k  += o.k
        hr  += o.hr;   tb   += o.tb;  app+= o.app
        qs  += o.qs;   h_a  += o.h;   bb_a += o.bb

    # Derive rate stats from accumulated components
    era  = round(er   * 9 / ip, 2) if ip > 0 else 0.0
    k9   = round(k    * 9 / ip, 2) if ip > 0 else 0.0
    h9   = round(h_a  * 9 / ip, 2) if ip > 0 else 0.0
    bb9  = round(bb_a * 9 / ip, 2) if ip > 0 else 0.0

    return {
        "APP": round(app), "ER": round(er, 1), "HR": round(hr),
        "K":   round(k),   "TB": round(tb, 1),
        "ERA": era,        "K/9": k9, "H/9": h9, "BB/9": bb9,
        "QS":  round(qs),  "_ip": round(ip, 1),
    }


# ---------------------------------------------------------------------------
# Hitting line builder
# Uses _hit suffix keys to match yahoo_client and rolling stats dict
# ---------------------------------------------------------------------------

def build_hitting_line(rolling: dict, remaining_games: int,
                        scenario: str) -> dict:
    """
    Project team hitting totals for remaining games this week.
    Key names use _hit suffix where collision risk exists (HR_hit, K_hit).
    """
    days = max(1, rolling.get("days_in_window", 21))
    per  = {c: rolling.get(c, 0) / days
            for c in ("R", "H", "HR_hit", "RBI", "SB", "BB", "K_hit", "TB")}

    cs = {"good": 1.12, "average": 1.0, "poor": 0.88}[scenario]
    rs = {"good": 1.0,  "average": 1.0, "poor": 0.96}[scenario]
    ks = {"good": 0.88, "average": 1.0, "poor": 1.12}[scenario]  # K_hit bad

    bk = {c: rolling.get(f"banked_{c}", 0)
          for c in ("R", "H", "HR_hit", "RBI", "SB", "BB", "K_hit", "TB")}

    result = {}
    for c in ("R", "H", "HR_hit", "RBI", "SB", "BB", "TB"):
        result[c] = round(bk[c] + per[c] * remaining_games * cs)
    result["K_hit"] = round(bk["K_hit"] + per["K_hit"] * remaining_games * ks)
    result["AVG"]   = round(rolling.get("AVG", 0.248) * rs, 3)
    result["OPS"]   = round(rolling.get("OPS", 0.715) * rs, 3)
    return result


# ---------------------------------------------------------------------------
# Category evaluator
# ---------------------------------------------------------------------------

def evaluate_categories(your_floor:   TeamWeekLine,
                         your_avg:     TeamWeekLine,
                         opp_ceiling:  TeamWeekLine,
                         opp_avg:      TeamWeekLine,
                         current_mine: dict,
                         current_opp:  dict) -> list:
    outcomes = []
    for cat, meta in ALL_CATS.items():
        hb = meta["higher_better"]

        yf = your_floor.get(cat)  or 0
        ya = your_avg.get(cat)    or 0
        oc = opp_ceiling.get(cat) or 0
        oa = opp_avg.get(cat)     or 0
        cm = current_mine.get(cat, 0) or 0
        co = current_opp.get(cat,  0) or 0

        def win(a, b):
            return (a >= b) if hb else (a <= b)

        cw  = win(cm, co)
        fbc = win(yf, oc)
        aba = win(ya, oa)

        if fbc:
            action = "SAFE"
            note   = "Win even at worst-case spread"
        elif aba and cw:
            action = "HEDGE"
            note   = "Winning but their great week could flip it"
        elif aba and not cw:
            action = "HOLD"
            note   = "Losing now, projected to flip — stay the course"
        else:
            if cat == "K" and cat in PITCHING_CATS:
                action = "STREAM_K"
            elif cat == "QS":
                action = "STREAM_QS"
            else:
                action = "NEED_HELP"
            note = "Losing projected — needs active management"

        outcomes.append(CatOutcome(
            cat=cat,
            your_avg_val=ya,   your_floor_val=yf,
            opp_avg_val=oa,    opp_ceil_val=oc,
            currently_winning=cw,
            floor_beats_ceil=fbc,
            avg_beats_avg=aba,
            action=action, note=note,
        ))
    return outcomes


# ---------------------------------------------------------------------------
# Start decision maker — asymmetric (your bad vs their ceiling)
# ---------------------------------------------------------------------------

def make_start_decision(proj: PitcherProjection,
                         cat_outcomes: list,
                         banked_ip: float,
                         other_starters_avg_ip: float) -> StartDecision:
    bad  = proj.bad
    avg  = proj.avg
    good = proj.good

    ip_without    = banked_ip + other_starters_avg_ip
    ip_floor_flag = ip_without < IP_MINIMUM

    must_reasons = []
    go_reasons   = []
    sit_reasons  = []

    if ip_floor_flag:
        needed = round(IP_MINIMUM - ip_without, 1)
        must_reasons.append(
            f"IP floor risk — {ip_without:.1f} projected without this start, "
            f"need {needed:.1f} more to hit {IP_MINIMUM:.0f} IP min"
        )

    def _opp_ceil(cat_name: str) -> Optional[float]:
        c = next((x for x in cat_outcomes if x.cat == cat_name), None)
        return c.opp_ceil_val if c else None

    def _you_winning(cat_name: str) -> bool:
        c = next((x for x in cat_outcomes if x.cat == cat_name), None)
        return c.currently_winning if c else False

    def _avg_winning(cat_name: str) -> bool:
        c = next((x for x in cat_outcomes if x.cat == cat_name), None)
        return c.avg_beats_avg if c else True

    era_ceil  = _opp_ceil("ERA")
    h9_ceil   = _opp_ceil("H/9")
    bb9_ceil  = _opp_ceil("BB/9")

    era_danger  = _you_winning("ERA")  and era_ceil  and bad.era  > era_ceil  + 0.25
    h9_danger   = _you_winning("H/9")  and h9_ceil   and bad.h9   > h9_ceil   + 1.00
    bb9_danger  = _you_winning("BB/9") and bb9_ceil  and bad.bb9  > bb9_ceil  + 0.80

    if era_danger:
        sit_reasons.append(
            f"Bad scenario ERA {bad.era} could flip ERA cat "
            f"(their ceiling: {era_ceil})"
        )
    else:
        go_reasons.append(f"ERA safe even in bad scenario ({bad.era})")

    if h9_danger:
        sit_reasons.append(f"Bad H/9 ({bad.h9}) threatens H/9 cat")
    if bb9_danger:
        sit_reasons.append(f"Bad BB/9 ({bad.bb9}) threatens BB/9 cat")

    if not _avg_winning("K") and avg.k >= 5:
        go_reasons.append(f"K upside needed — avg scenario adds {avg.k}K")
    if good.k >= 9:
        go_reasons.append(f"K ceiling: good scenario = {good.k}K")
    if avg.qs >= 1:
        go_reasons.append("QS likely in avg scenario")

    rank = proj.opp_offense_rank
    if rank <= 5:
        sit_reasons.append(
            f"Top-5 offense ({proj.opponent}, rank #{rank}) — outcomes shift worse"
        )
    elif rank >= 21:
        go_reasons.append(f"Weak offense ({proj.opponent}, rank #{rank})")

    if proj.data_quality in ("THIN", "FALLBACK"):
        go_reasons.append(
            f"⚠️ Only {proj.log_count} appearances in history — less reliable"
        )

    go  = len(must_reasons) * 3 + len(go_reasons)
    sit = len(sit_reasons)

    if must_reasons:
        rec, conf = "MUST_START", "HIGH"
    elif go >= sit + 3:
        rec  = "START"
        conf = "HIGH" if go >= sit + 5 else "MEDIUM"
    elif sit >= go + 2:
        rec  = "SIT"
        conf = "HIGH" if sit >= go + 4 else "MEDIUM"
    else:
        rec, conf = "CONDITIONAL", "LOW"

    if ip_floor_flag and rec == "SIT":
        rec, conf = "CONDITIONAL", "MEDIUM"
        go_reasons.append("⚠️ Can't bench — IP floor at risk")

    reasoning = " | ".join(must_reasons + go_reasons + sit_reasons) \
                or "No strong signal."

    return StartDecision(
        name=proj.name, team=proj.team,
        opponent=proj.opponent, opp_offense_rank=proj.opp_offense_rank,
        recommendation=rec, confidence=conf, reasoning=reasoning,
        ip_floor_flag=ip_floor_flag, projection=proj,
    )


# ---------------------------------------------------------------------------
# Streaming finder (SP only)
# ---------------------------------------------------------------------------

def find_streamers(fa_pitchers: list, cat_outcomes: list,
                    remaining_games: int, mlb_client,
                    top_n: int = 4) -> list:
    def _opp_ceil(cat_name):
        c = next((x for x in cat_outcomes if x.cat == cat_name), None)
        return c.opp_ceil_val if c else 99.0

    def _winning(cat_name):
        c = next((x for x in cat_outcomes if x.cat == cat_name), None)
        return c.currently_winning if c else False

    def _avg_winning(cat_name):
        c = next((x for x in cat_outcomes if x.cat == cat_name), None)
        return c.avg_beats_avg if c else True

    candidates = []

    for p in fa_pitchers:
        try:
            pos = p.get("position", "")
            if "SP" not in pos:
                continue

            mlb_id = p.get("mlb_id")
            if not mlb_id:
                continue

            proj = project_pitcher(p, mlb_id, remaining_games, mlb_client)
            if not proj or proj.is_rp:
                continue

            bad = proj.bad

            era_safe = (not _winning("ERA") or bad.era <= _opp_ceil("ERA") + 0.15)
            h9_safe  = (not _winning("H/9") or bad.h9  <= _opp_ceil("H/9") + 0.80)
            if not (era_safe and h9_safe):
                continue

            score = 0
            rank  = proj.opp_offense_rank
            if rank >= 26: score += 4
            elif rank >= 21: score += 2
            elif rank <= 5: score -= 3
            if proj.avg.k  >= 7:  score += 2
            if proj.avg.k  >= 9:  score += 1
            if proj.avg.qs >= 1:  score += 1
            if proj.avg.ip >= 6:  score += 1
            if not _avg_winning("K")  and proj.avg.k  >= 6: score += 2
            if not _avg_winning("QS") and proj.avg.qs >= 1: score += 2
            if proj.data_quality == "FALLBACK": score -= 2
            if score < 2:
                continue

            grade   = "A" if score >= 7 else ("B" if score >= 4 else "C")
            primary = (
                "K_UPSIDE"  if not _avg_winning("K")  and proj.avg.k  >= 6 else
                "QS_CHANCE" if not _avg_winning("QS") and proj.avg.qs >= 1 else
                "IP_FILLER" if proj.avg.ip >= 6 else "ERA_SAFE"
            )

            reasons = []
            if rank >= 21:
                reasons.append(f"Soft matchup vs {proj.opponent} (rank #{rank})")
            reasons.append(f"Avg: {proj.avg.ip}IP / {proj.avg.k}K / {proj.avg.er}ER")
            if proj.avg.qs >= 1:
                reasons.append("QS likely")
            if proj.data_quality in ("THIN", "FALLBACK"):
                reasons.append(f"⚠️ Limited data ({proj.log_count} apps)")

            candidates.append(StreamerRec(
                name=p["name"], team=p.get("team", ""),
                opponent=proj.opponent,
                opp_offense_rank=rank,
                matchup_grade=grade,
                projection=proj,
                primary_value=primary,
                reasoning=". ".join(reasons),
            ))
        except Exception as e:
            print(f"[engine] Streamer eval failed {p.get('name','?')}: {e}")

    candidates.sort(key=lambda c: (
        {"A": 0, "B": 1, "C": 2}[c.matchup_grade],
        -c.projection.avg.k
    ))
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# IP plan
# ---------------------------------------------------------------------------

def build_ip_plan(banked: float, sp_projections: list,
                   streamers: list) -> IPPlan:
    roster_ip = sum(p.avg.ip for p in sp_projections)
    total     = banked + roster_ip
    shortfall = max(0.0, round(IP_MINIMUM - total, 1))
    needed    = math.ceil(shortfall / 5.0) if shortfall > 0 else 0

    if shortfall == 0:
        note = (f"IP minimum covered — {total:.1f} projected "
                f"({banked:.1f} banked + {roster_ip:.1f} from roster starters)")
    else:
        note = (f"⚠️ {shortfall:.1f} IP short of {IP_MINIMUM:.0f} min. "
                f"Need ~{needed} SP streaming start(s) averaging 5+ IP.")
        if streamers:
            names = ", ".join(s.name for s in streamers[:needed])
            note += f" Best options: {names}"

    return IPPlan(
        banked=banked, roster_projected=round(roster_ip, 1),
        total_projected=round(total, 1), shortfall=shortfall,
        streamers_needed=needed, note=note,
    )


# ---------------------------------------------------------------------------
# Week summary
# ---------------------------------------------------------------------------

def build_summary(cat_outcomes: list, ip_plan: IPPlan,
                   sp_decisions: list,
                   current_score_you: int = 0,
                   current_score_opp: int = 0,
                   opponent_name: str = "OPP",
                   score_as_of: str = "") -> str:
    safe      = [c for c in cat_outcomes if c.action == "SAFE"]
    hedge     = [c for c in cat_outcomes if c.action == "HEDGE"]
    need_help = [c for c in cat_outcomes
                 if c.action in ("NEED_HELP", "STREAM_K", "STREAM_QS")]
    must_starts = [d for d in sp_decisions if d.recommendation == "MUST_START"]
    must_names  = ", ".join(d.name for d in must_starts) if must_starts else "None"

    # IP status
    ip_banked  = ip_plan.banked
    ip_needed  = max(0, IP_MINIMUM - ip_banked)
    starts_est = math.ceil(ip_needed / 5.0) if ip_needed > 0 else 0
    ip_line = (
        f"✅ IP minimum covered ({ip_banked:.1f}/{IP_MINIMUM:.0f} IP banked)"
        if ip_needed == 0
        else f"⚠️ {ip_needed:.1f} IP short of {IP_MINIMUM:.0f} min — need ~{starts_est} more SP start(s)"
    )

    # Current score
    score_line = ""
    if score_as_of:
        if current_score_you > current_score_opp:
            leader = "WAR leads"
        elif current_score_opp > current_score_you:
            leader = f"{opponent_name} leads"
        else:
            leader = "Tied"
        score_line = (
            f"Current score: {leader} "
            f"{max(current_score_you, current_score_opp)}–"
            f"{min(current_score_you, current_score_opp)} "
            f"(through {score_as_of}). "
        )

    winning = [c for c in cat_outcomes if c.currently_winning]
    parts = [
        score_line,
        f"Projected {len(winning)}/20 categories in your favor (avg vs avg). ",
        f"Safe at worst-case: {', '.join(c.cat for c in safe)}. " if safe else "",
        (f"Vulnerable: {', '.join(c.cat for c in hedge)} — "
         f"one bad pitching stretch flips these. " if hedge else ""),
        (f"Need help: {', '.join(c.cat for c in need_help)}. "
         if need_help else ""),
        ip_line + ". ",
        f"Must-start remaining: {must_names}.",
    ]
    return "".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def render_scorecard(plan: WeekPlan) -> str:
    html = ['<div class="matchup-engine">']
    # Header with opponent name and current score
    score_display = (
        f'<span style="color:#27ae60;font-weight:700">WAR {plan.current_score_you}</span>'
        f' — '
        f'<span style="color:#e74c3c;font-weight:700">{plan.opponent_name} {plan.current_score_opp}</span>'
    )
    score_note = (
        f' <span style="font-size:0.72rem;color:#9a9a94;font-weight:400">through {plan.score_as_of}</span>'
        if plan.score_as_of else ""
    )
    html.append(
        f'<h2>📊 WAR vs {plan.opponent_name}{score_note}<br>'
        f'<span style="font-size:1rem;font-weight:600">{score_display}</span></h2>'
    )

    # IP banner
    ip  = plan.ip_plan
    pct = min(100, int(ip.banked / IP_MINIMUM * 100))
    bar_color = ("#e74c3c" if pct < 50 else
                 "#f39c12" if pct < 80 else "#27ae60")
    html.append(f'''
    <div class="ip-banner">
      <span class="ip-label">IP Banked: {ip.banked:.1f} / {IP_MINIMUM:.0f}</span>
      <div class="ip-bar-bg">
        <div class="ip-bar" style="width:{pct}%;background:{bar_color}"></div>
      </div>
      <span class="ip-note">{ip.note}</span>
    </div>''')

    # 20-cat scorecard
    html.append('<h3>20-Cat Projected Final</h3>')
    html.append(
        '<p class="scenario-note">'
        'Your avg→poor vs Opponent good→avg &nbsp;|&nbsp; '
        '🟢 Win &nbsp; 🟡 Hedge &nbsp; 🔴 Need help'
        '</p>'
    )
    html.append('<table class="scorecard">')
    html.append(
        '<tr><th>Cat</th>'
        '<th>You avg</th><th>You floor</th>'
        '<th>Opp avg</th><th>Opp ceil</th>'
        '<th>Now</th><th>Proj</th><th>Action</th></tr>'
    )

    AC = {"SAFE": "#27ae60", "HOLD": "#27ae60", "HEDGE": "#f39c12",
          "NEED_HELP": "#e74c3c", "STREAM_K": "#e74c3c", "STREAM_QS": "#e74c3c"}
    AI = {"SAFE": "✅", "HOLD": "✅", "HEDGE": "⚠️",
          "NEED_HELP": "🚨", "STREAM_K": "🎯", "STREAM_QS": "🎯"}

    # Display labels for cats with internal suffix names
    CAT_DISPLAY = {
        "HR_hit": "HR", "K_hit": "K (bat)",
        "K": "K (pit)", "HR": "HR (pit)", "TB": "TB",
    }

    for section, cats in [("⚔️ Hitting", HITTING_CATS),
                           ("🎯 Pitching", PITCHING_CATS)]:
        html.append(f'<tr class="section-row"><td colspan="8">{section}</td></tr>')
        for cat in cats:
            c = next((x for x in plan.cat_outcomes if x.cat == cat), None)
            if not c:
                continue
            is_rate  = ALL_CATS[cat]["rate"]
            fmt      = ".3f" if is_rate else ".0f"
            color    = AC.get(c.action, "#666")
            icon     = AI.get(c.action, "—")
            display  = CAT_DISPLAY.get(cat, cat)
            html.append(
                f'<tr>'
                f'<td class="cat-name">{display}</td>'
                f'<td>{format(c.your_avg_val,   fmt)}</td>'
                f'<td style="color:{color}">{format(c.your_floor_val, fmt)}</td>'
                f'<td>{format(c.opp_avg_val,    fmt)}</td>'
                f'<td>{format(c.opp_ceil_val,   fmt)}</td>'
                f'<td>{"🟢" if c.currently_winning else "🔴"}</td>'
                f'<td>{"🟢" if c.avg_beats_avg   else "🔴"}</td>'
                f'<td style="color:{color}">{icon} {c.action}</td>'
                f'</tr>'
            )
    html.append('</table>')

    # SP start decisions
    sp_decisions = [d for d in plan.start_decisions if not d.projection.is_rp]
    rp_projs     = [d.projection for d in plan.start_decisions if d.projection.is_rp]

    RC  = {"MUST_START": "#2980b9", "START": "#27ae60",
           "CONDITIONAL": "#f39c12", "SIT": "#e74c3c"}
    DQC = {"STRONG": "#27ae60", "OK": "#f39c12",
           "THIN": "#e74c3c",   "FALLBACK": "#e74c3c"}

    if sp_decisions:
        html.append('<h3>⚾ SP Start Decisions</h3>')
        html.append('<div class="start-decisions">')
        for d in sp_decisions:
            p = d.projection
            # Date badge
            if d.start_date_label == "Today":
                date_badge = '<span class="rec-badge" style="background:#fce8e6;color:#c5221f">Today</span>'
            elif d.start_date_label == "Tomorrow":
                date_badge = '<span class="rec-badge" style="background:#fff3cd;color:#856404">Tomorrow</span>'
            elif d.start_date_label:
                date_badge = f'<span class="rec-badge" style="background:#f1f0ec;color:#5a5a54">{d.start_date_label}</span>'
            else:
                date_badge = ""
            # Opp line with K%
            k_pct_str    = f" · K% {d.opp_k_pct:.1f}%" if d.opp_k_pct else ""
            opp_line_html = (
                f'<div class="opp-line">vs {d.opponent} '
                f'(offense rank #{d.opp_offense_rank}){k_pct_str}</div>'
            )
            dq_color  = DQC[p.data_quality]
            bad_qs    = "  ✅QS" if p.bad.qs  else ""
            avg_qs    = "  ✅QS" if p.avg.qs  else ""
            good_qs   = "  ✅QS" if p.good.qs else ""
            bad_line  = f'{p.bad.ip}IP / {p.bad.k}K / {p.bad.er}ER / ERA {p.bad.era} / H9 {p.bad.h9} / BB9 {p.bad.bb9}{bad_qs}'
            avg_line  = f'{p.avg.ip}IP / {p.avg.k}K / {p.avg.er}ER / ERA {p.avg.era} / H9 {p.avg.h9} / BB9 {p.avg.bb9}{avg_qs}'
            good_line = f'{p.good.ip}IP / {p.good.k}K / {p.good.er}ER / ERA {p.good.era} / H9 {p.good.h9} / BB9 {p.good.bb9}{good_qs}'
            ip_flag_html = '<span class=ip-flag>⚠️ IP</span>' if d.ip_floor_flag else ""
            html.append(
                f'<div class="start-card">'
                f'<div class="start-header">'
                f'<span class="pitcher-name">{d.name}</span>'
                f'<span class="rec-badge pill-{d.recommendation.lower().replace("_","-")}">'
                f'{d.recommendation}</span>'
                f'{date_badge}'
                f'<span class="conf-badge">{d.confidence}</span>'
                f'<span class="dq-badge" style="color:{dq_color}">'
                f'{p.log_count} apps ({p.data_quality})</span>'
                f'{ip_flag_html}'
                f'</div>'
                f'{opp_line_html}'
                f'<div class="scenarios">'
                f'<div class="scenario bad">🔴 Bad: &nbsp;{bad_line}</div>'
                f'<div class="scenario avg">🟡 Avg: &nbsp;{avg_line}</div>'
                f'<div class="scenario good">🟢 Good: {good_line}</div>'
                f'</div>'
                f'<div class="reasoning">{d.reasoning}</div>'
                f'</div>'
            )
        html.append('</div>')

    # Bullpen summary (replaces RP table)
    if plan.bullpen_summary:
        html.append(
            f'<div class="alert-bar" style="margin-top:12px;margin-bottom:4px;">'
            f'🔥 <b>Bullpen:</b> {plan.bullpen_summary}'
            f'</div>'
        )

    if plan.streamers:
        html.append('<h3>🎯 SP Streaming Targets</h3>')
        html.append('<p class="streamer-note">Bad scenario does not flip '
                    'ERA, H/9, or BB/9.</p>')
        html.append('<div class="streamers">')
        GC = {"A": "#27ae60", "B": "#f39c12", "C": "#95a5a6"}
        for s in plan.streamers:
            p = s.projection
            html.append(
                f'<div class="streamer-card">'
                f'<span class="streamer-name">{s.name} ({s.team})</span>'
                f'<span class="grade-badge" style="background:{GC[s.matchup_grade]}">'
                f'{s.matchup_grade}</span>'
                f'<span class="value-tag">{s.primary_value.replace("_"," ")}</span>'
                f'<div class="streamer-opp">vs {s.opponent} '
                f'(rank #{s.opp_offense_rank}) | {p.log_count} apps ({p.data_quality})</div>'
                f'<div class="streamer-line">'
                f'Bad: {p.bad.ip}IP/{p.bad.k}K/{p.bad.er}ER | '
                f'Avg: {p.avg.ip}IP/{p.avg.k}K/{p.avg.er}ER | '
                f'Good: {p.good.ip}IP/{p.good.k}K/{p.good.er}ER</div>'
                f'<div class="streamer-reason">{s.reasoning}</div>'
                f'</div>'
            )
        html.append('</div>')
    else:
        html.append('<p class="no-streamers">🔎 No safe SP streaming options this week.</p>')

    html.append(
        f'<div class="week-summary">'
        f'<h3>📋 Week Plan</h3>'
        f'<p>{plan.summary}</p>'
        f'</div>'
    )
    html.append('</div>')
    return "\n".join(html)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_weekly_matchup_section(yahoo_client,
                                mlb_client,
                                team_offense_ranker,
                                combined_players: list) -> str:
    try:
        mlb_id_map = {p["name"]: p.get("mlb_id")
                      for p in combined_players if p.get("mlb_id")}

        matchup             = yahoo_client.get_current_matchup_full()
        my_banked           = matchup.get("my_stats",  {})
        opp_banked          = matchup.get("opp_stats", {})
        banked_ip           = float(my_banked.get("IP", 0.0))
        my_remaining_games  = matchup.get("my_remaining_games",  3)
        opp_remaining_games = matchup.get("opp_remaining_games", 3)
        opponent_name       = matchup.get("opponent_team_name", "OPP")
        current_score_you   = matchup.get("current_score_you",  0)
        current_score_opp   = matchup.get("current_score_opp",  0)
        score_as_of         = matchup.get("score_as_of", "")

        my_rolling  = yahoo_client.get_team_rolling_hitting_stats(
            is_opponent=False, mlb_id_map=mlb_id_map, mlb_client=mlb_client)
        opp_rolling = yahoo_client.get_team_rolling_hitting_stats(
            is_opponent=True,  mlb_id_map=mlb_id_map, mlb_client=mlb_client)

        my_pitchers  = yahoo_client.get_pitchers_with_remaining_starts(False)
        opp_pitchers = yahoo_client.get_pitchers_with_remaining_starts(True)

        # Game date lookup for start_date_label on SP cards
        pitcher_game_dates = {p["name"]: p.get("game_date", "") for p in my_pitchers}

        def project_rotation(pitchers, remaining_games):
            projs = []
            for p in pitchers:
                mlb_id = mlb_id_map.get(p["name"])
                if not mlb_id:
                    continue
                p["opp_rank"] = team_offense_ranker.get_offense_rank(
                    p.get("opponent", ""))
                proj = project_pitcher(p, mlb_id, remaining_games, mlb_client)
                if proj:
                    projs.append(proj)
            return projs

        my_projs  = project_rotation(my_pitchers,  my_remaining_games)
        opp_projs = project_rotation(opp_pitchers, opp_remaining_games)

        def make_lines(projs, banked, hit_rolling, rem_games):
            lines = {}
            for sc, hit_sc, pitch_sc in [
                ("floor",   "poor",    "bad"),
                ("average", "average", "avg"),
                ("ceiling", "good",    "good"),
            ]:
                pitch = aggregate_pitching_line(projs, pitch_sc, banked)
                hit   = build_hitting_line(hit_rolling, rem_games, hit_sc)
                lines[sc] = TeamWeekLine(
                    scenario=sc, hitting=hit, pitching=pitch,
                    ip_total=pitch.get("_ip", 0.0),
                )
            return lines

        my_lines  = make_lines(my_projs,  my_banked,  my_rolling,  my_remaining_games)
        opp_lines = make_lines(opp_projs, opp_banked, opp_rolling, opp_remaining_games)

        your_floor  = my_lines["floor"]
        your_avg    = my_lines["average"]
        opp_ceiling = opp_lines["ceiling"]
        opp_avg     = opp_lines["average"]

        cat_outcomes = evaluate_categories(
            your_floor, your_avg, opp_ceiling, opp_avg,
            my_banked, opp_banked,
        )

        sp_projs       = [p for p in my_projs if not p.is_rp]
        avg_ip_by_name = {p.name: p.avg.ip for p in sp_projs}

        start_decisions = []
        for proj in my_projs:
            if proj.is_rp:
                start_decisions.append(StartDecision(
                    name=proj.name, team=proj.team,
                    opponent=proj.opponent,
                    opp_offense_rank=proj.opp_offense_rank,
                    recommendation="START", confidence="MEDIUM",
                    reasoning=(f"Expected {proj.expected_apps:.1f} appearances "
                                f"this week based on workload rate and recent usage."),
                    ip_floor_flag=False, projection=proj,
                ))
            else:
                other_ip = sum(ip for n, ip in avg_ip_by_name.items()
                               if n != proj.name)
                dec = make_start_decision(proj, cat_outcomes, banked_ip, other_ip)

                # Populate start date label from game_date dict
                gd_str = pitcher_game_dates.get(proj.name, "")
                try:
                    gd = date.fromisoformat(gd_str) if gd_str else None
                except ValueError:
                    gd = None
                if gd:
                    if gd == TODAY:
                        dec.start_date_label = "Today"
                    elif gd == TODAY + timedelta(days=1):
                        dec.start_date_label = "Tomorrow"
                    else:
                        dec.start_date_label = gd.strftime("%a %b %-d")

                # Populate opposing team K% from offense ranker
                opp_grade     = team_offense_ranker.get_matchup_grade(proj.opponent)
                dec.opp_k_pct = opp_grade.get("k_rate") or 0.0

                start_decisions.append(dec)

        fa_raw = yahoo_client.get_fa_pitchers_with_starts()
        for p in fa_raw:
            p["mlb_id"] = mlb_id_map.get(p["name"])
        fa_raw = [p for p in fa_raw if p.get("mlb_id")]

        streamers = find_streamers(
            fa_raw, cat_outcomes, my_remaining_games, mlb_client
        )

        ip_plan = build_ip_plan(banked_ip, sp_projs, streamers)

        # Bullpen summary (replaces RP table)
        from src.analysis.pitcher_analyzer import build_bullpen_summary
        rp_list = [{"name": p.name, "expected_apps": p.expected_apps}
                   for p in my_projs if p.is_rp]
        bullpen_summary = build_bullpen_summary(rp_list, cat_outcomes)

        sp_decisions_only = [d for d in start_decisions if not d.projection.is_rp]
        summary = build_summary(
            cat_outcomes, ip_plan, sp_decisions_only,
            current_score_you=current_score_you,
            current_score_opp=current_score_opp,
            opponent_name=opponent_name,
            score_as_of=score_as_of,
        )

        plan = WeekPlan(
            your_floor=your_floor, your_avg=your_avg,
            opp_ceiling=opp_ceiling, opp_avg=opp_avg,
            cat_outcomes=cat_outcomes,
            start_decisions=start_decisions,
            streamers=streamers,
            ip_plan=ip_plan,
            summary=summary,
            opponent_name=opponent_name,
            current_score_you=current_score_you,
            current_score_opp=current_score_opp,
            score_as_of=score_as_of,
            bullpen_summary=bullpen_summary,
        )
        return render_scorecard(plan)

    except Exception as e:
        import traceback
        print(f"[weekly_matchup_engine] Fatal: {e}")
        traceback.print_exc()
        return f"<p>⚠️ Weekly matchup projection unavailable: {e}</p>"
