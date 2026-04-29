"""
weekly_matchup_evaluator.py
FBP Email Digest — Category evaluator + start/streamer decisions.

Lifted from weekly_matchup_engine.py:
  - League scoring categories + IP minimum
  - Team week line / cat outcome / start decision / streamer / IP plan / week plan dataclasses
  - Hitting and pitching aggregators
  - Category evaluator (your-floor vs opp-ceiling logic)
  - SP start decision (must-start / start / conditional / sit)
  - SP streamer finder
  - IP plan
  - Week plan summary text

Imports projection types and helpers from weekly_matchup_projection.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from src.data.weekly_matchup_projection import (
    AppearanceLog,
    AppOutcome,
    PitcherProjection,
    TODAY,
    _fmt_ip,
    project_pitcher,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IP_MINIMUM = 35.0

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
    "TB_hit": {"higher_better": True,  "rate": False},  # batting total bases
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
    your_avg_val:      float   # season baseline (rate × 7, no banked)
    your_floor_val:    float   # worst-case end-of-week
    your_exp_val:      float   # banked + remaining projection
    your_current_val:  float   # currently banked
    opp_ceil_val:      float   # opp best-case end-of-week
    opp_avg_val:       float   # opp season baseline (rate × 7, no banked)
    opp_exp_val:       float   # opp banked + remaining projection
    opp_current_val:   float   # opp currently banked
    currently_winning: bool    # winning on current banked totals
    floor_beats_ceil:  bool    # your floor beats their ceiling
    avg_beats_avg:     bool    # your exp beats opp exp (used for action logic)
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
    tb   = float(banked.get("TB",         0))   # TB allowed
    app  = float(banked.get("APP",        0))
    qs   = float(banked.get("QS",         0))
    h_a  = float(banked.get("H_allowed",  0))
    bb_a = float(banked.get("BB_allowed", 0))

    # The league scores H/9 and BB/9 directly — raw H_allowed/BB_allowed
    # aren't returned in the matchup endpoint. Back-derive them from the
    # banked rate stats so accumulated projection rates are accurate.
    if h_a == 0 and ip > 0 and float(banked.get("H/9", 0)) > 0:
        h_a = float(banked["H/9"]) * ip / 9.0
    if bb_a == 0 and ip > 0 and float(banked.get("BB/9", 0)) > 0:
        bb_a = float(banked["BB/9"]) * ip / 9.0

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
            for c in ("R", "H", "HR_hit", "RBI", "SB", "BB", "K_hit", "TB_hit")}

    cs = {"good": 1.12, "average": 1.0, "poor": 0.88}[scenario]
    rs = {"good": 1.0,  "average": 1.0, "poor": 0.96}[scenario]
    ks = {"good": 0.88, "average": 1.0, "poor": 1.12}[scenario]  # K_hit bad

    bk = {c: rolling.get(f"banked_{c}", 0)
          for c in ("R", "H", "HR_hit", "RBI", "SB", "BB", "K_hit", "TB_hit")}

    result = {}
    for c in ("R", "H", "HR_hit", "RBI", "SB", "BB", "TB_hit"):
        result[c] = round(bk[c] + per[c] * remaining_games * cs)
    result["K_hit"] = round(bk["K_hit"] + per["K_hit"] * remaining_games * ks)
    result["AVG"]   = round(rolling.get("AVG", 0.248) * rs, 3)
    result["OPS"]   = round(rolling.get("OPS", 0.715) * rs, 3)
    return result


# ---------------------------------------------------------------------------
# Category evaluator
# ---------------------------------------------------------------------------

def evaluate_categories(your_floor:        TeamWeekLine,
                         your_exp:          TeamWeekLine,
                         your_avg_baseline: TeamWeekLine,
                         opp_ceiling:       TeamWeekLine,
                         opp_exp:           TeamWeekLine,
                         opp_avg_baseline:  TeamWeekLine,
                         current_mine:      dict,
                         current_opp:       dict) -> list:
    """
    your_exp          = banked + remaining projection (action logic source)
    your_avg_baseline = season rate × 7 days (display only — Avg column)
    opp_exp           = opp banked + remaining projection
    opp_avg_baseline  = opp season rate × 7 (display only)
    """
    outcomes = []
    for cat, meta in ALL_CATS.items():
        hb = meta["higher_better"]

        # Floor: rate-based worst case — consistent with avg_baseline (no banked).
        # Uses the avg_baseline value scaled by the floor scenario multiplier so
        # Floor, Avg, and Ceil are all on the same basis (pure rate × 7).
        # Exp is the only column that adds banked stats to remaining projection.
        ya_raw = your_avg_baseline.get(cat) or 0
        meta_info = ALL_CATS[cat]
        if meta_info["rate"]:
            # Rate stats (AVG, OPS, ERA, etc.): small directional nudge
            floor_mult = 0.96 if meta_info["higher_better"] else 1.04
            yf = round(ya_raw * floor_mult, 2)
        else:
            # Counting stats: 12% swing matches the "poor" scenario multiplier
            floor_mult = 0.88 if meta_info["higher_better"] else 1.12
            yf = round(ya_raw * floor_mult)

        ye  = your_exp.get(cat)          or 0   # exp = action logic
        ya  = your_avg_baseline.get(cat) or 0   # season baseline display

        # Ceil: opp rate-based best case — consistent with opp_avg_baseline (no banked).
        oa_raw = opp_avg_baseline.get(cat) or 0
        if meta_info["rate"]:
            ceil_mult = 1.04 if meta_info["higher_better"] else 0.96
            oc = round(oa_raw * ceil_mult, 2)
        else:
            ceil_mult = 1.12 if meta_info["higher_better"] else 0.88
            oc = round(oa_raw * ceil_mult)

        oe  = opp_exp.get(cat)           or 0   # opp exp = action logic
        oa  = opp_avg_baseline.get(cat)  or 0   # opp season baseline display
        cm  = current_mine.get(cat, 0)   or 0   # your banked
        co  = current_opp.get(cat, 0)   or 0    # opp banked

        def win(a, b):
            return (a >= b) if hb else (a <= b)

        cw  = win(cm, co)    # currently winning on banked
        fbc = win(yf, oc)    # floor beats their ceiling
        aba = win(ye, oe)    # exp beats their exp (was avg_beats_avg)

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
            your_avg_val=ya,     your_floor_val=yf,
            your_exp_val=ye,     your_current_val=cm,
            opp_ceil_val=oc,
            opp_avg_val=oa,      opp_exp_val=oe,
            opp_current_val=co,
            currently_winning=cw,
            floor_beats_ceil=fbc,
            avg_beats_avg=aba,
            action=action, note=note,
        ))
    return outcomes


# ---------------------------------------------------------------------------
# Natural-language reasoning builder
# ---------------------------------------------------------------------------

def _build_reasoning(ip_floor_flag: bool, proj: PitcherProjection,
                     cat_outcomes: list) -> str:
    parts = []

    # 1. Recent performance context
    if proj.last_3:
        recent_er = [lg.er for lg in proj.last_3]
        recent_ip = [lg.ip for lg in proj.last_3]
        worst_er  = max(recent_er)
        worst_ip  = min(recent_ip)   # shortest outing (blow-up indicator)
        avg_er    = sum(recent_er) / len(recent_er)
        avg_ip    = sum(recent_ip) / len(recent_ip)

        if worst_ip < 2.5 and worst_er >= 5:
            # Named blow-up: short outing with high damage — most important data point
            blown = next(
                (lg for lg in proj.last_3 if lg.er == worst_er and lg.ip == worst_ip),
                proj.last_3[0],
            )
            parts.append(
                f"Last {len(proj.last_3)} appearances include a blow-up "
                f"({_fmt_ip(blown.ip)} IP / {blown.er} ER on {blown.date}) — "
                f"bad scenario fully reflects that tail risk."
            )
        elif worst_er >= 6:
            parts.append(
                f"Last {len(proj.last_3)} starts: avg {_fmt_ip(avg_ip)}IP, "
                f"{avg_er:.1f}ER — including a {worst_er}-ER outing that "
                f"widens the bad scenario."
            )
        elif avg_er <= 2.0:
            parts.append(
                f"Last {len(proj.last_3)} starts: avg {_fmt_ip(avg_ip)}IP, "
                f"{avg_er:.1f}ER — strong recent form supports avg/good scenarios."
            )
        else:
            parts.append(
                f"Last {len(proj.last_3)} starts: avg {_fmt_ip(avg_ip)}IP, "
                f"{avg_er:.1f}ER."
            )
    elif proj.data_quality in ("THIN", "FALLBACK"):
        parts.append(
            f"Only {proj.log_count} MLB appearances — less reliable; "
            f"widen mental error bars on the scenarios."
        )

    # 2. Matchup context
    rank = proj.opp_offense_rank
    if rank <= 5:
        parts.append(f"Tough matchup: {proj.opponent} ranks top-5 in offense.")
    elif rank >= 25:
        parts.append(f"Favorable draw: {proj.opponent} ranks bottom-5 in offense.")
    else:
        parts.append(f"{proj.opponent} is a mid-tier offense (rank #{rank}).")

    # 3. Category stakes
    k_out   = next((c for c in cat_outcomes if c.cat == "K"),   None)
    qs_out  = next((c for c in cat_outcomes if c.cat == "QS"),  None)
    era_out = next((c for c in cat_outcomes if c.cat == "ERA"), None)
    stakes  = []
    if k_out and k_out.action in ("NEED_HELP", "STREAM_K"):
        stakes.append(f"K is a losing cat — avg adds {proj.avg.k}K")
    if qs_out and qs_out.action in ("NEED_HELP", "STREAM_QS") and proj.avg.qs:
        stakes.append("QS likely in avg scenario")
    if era_out and era_out.action == "NEED_HELP":
        stakes.append(f"ERA losing — bad scenario ERA {proj.bad.era:.2f} makes it worse")
    if stakes:
        parts.append("Cat stakes: " + "; ".join(stakes) + ".")

    # 4. IP floor note
    if ip_floor_flag:
        parts.append("IP min at risk — start counts toward the 35-IP floor.")

    return " ".join(parts) if parts else "No strong signal."


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

    # Rate-stat danger flags have two guards:
    #
    # Guard 1 — minimum IP banked. With 0 IP banked both teams show 0.00 ERA,
    #           making "currently winning ERA" trivially true and firing the
    #           danger flag for every pitcher before a single inning is thrown.
    #           Only enable rate-stat flags once 5+ IP is on the board.
    #
    # Guard 2 — compare to opp_avg_val, not opp_ceil_val. The ceiling is the
    #           opponent's absolute best-case ERA for the full week (e.g. 0.57).
    #           Using it as the threshold makes every bad scenario look
    #           catastrophic. The relevant question is: does this pitcher's
    #           bad scenario beat their AVERAGE expected ERA? If yes, it's a
    #           manageable risk.
    rate_flags_active = banked_ip >= 5.0

    def _opp_avg(cat_name: str) -> Optional[float]:
        c = next((x for x in cat_outcomes if x.cat == cat_name), None)
        return c.opp_avg_val if c else None

    era_threshold  = _opp_avg("ERA")
    h9_threshold   = _opp_avg("H/9")
    bb9_threshold  = _opp_avg("BB/9")

    era_danger  = rate_flags_active and _you_winning("ERA")  and era_threshold  and bad.era  > era_threshold  + 0.50
    h9_danger   = rate_flags_active and _you_winning("H/9")  and h9_threshold   and bad.h9   > h9_threshold   + 1.50
    bb9_danger  = rate_flags_active and _you_winning("BB/9") and bb9_threshold  and bad.bb9  > bb9_threshold  + 1.00

    if era_danger:
        sit_reasons.append(
            f"Bad scenario ERA {bad.era:.2f} risks ERA cat "
            f"(their avg: {era_threshold:.2f})"
        )
    else:
        go_reasons.append(f"ERA safe even in bad scenario ({bad.era})")

    if h9_danger:
        sit_reasons.append(f"Bad H/9 ({bad.h9:.2f}) risks H/9 cat (their avg: {h9_threshold:.2f})")
    if bb9_danger:
        sit_reasons.append(f"Bad BB/9 ({bad.bb9:.2f}) risks BB/9 cat (their avg: {bb9_threshold:.2f})")

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

    reasoning = _build_reasoning(ip_floor_flag, proj, cat_outcomes)

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

            rank = proj.opp_offense_rank
            # Hard-exclude top-5 offenses regardless of pitcher strength.
            # A mere score penalty is not enough — e.g. Eury Pérez vs SF
            # (rank 5) was still passing through because of K upside.
            print(f"  [streamer] {p.get('name','?')} opp={proj.opponent!r} rank={rank}")
            if rank <= 5:
                print(f"  [streamer] → excluded (top-5 offense)")
                continue
            # Belt-and-suspenders: if the ranker returned a neutral default,
            # the opponent name from the MLB schedule may not match the
            # ranker's expected abbr format. Exclude by substring for the
            # handful of elite offenses we know are top-5 in 2026.
            EXCLUDED_OPPONENT_NAMES = (
                "san francisco", "giants", "sfg", "sf",
                "los angeles dodgers", "dodgers", "lad",
                "new york yankees", "yankees", "nyy",
                "atlanta braves", "braves", "atl",
                "philadelphia phillies", "phillies", "phi",
            )
            opp_lower = (proj.opponent or "").lower()
            if any(excl in opp_lower for excl in EXCLUDED_OPPONENT_NAMES):
                # Only trigger when rank lookup likely defaulted (rank 10-20).
                # If ranker already classified it top-5 we hit the guard above.
                if 10 <= rank <= 20:
                    print(f"  [streamer] → excluded ({proj.opponent!r} name match, rank={rank} looks defaulted)")
                    continue

            bad = proj.bad

            era_safe = (not _winning("ERA") or bad.era <= _opp_ceil("ERA") + 0.15)
            h9_safe  = (not _winning("H/9") or bad.h9  <= _opp_ceil("H/9") + 0.80)
            if not (era_safe and h9_safe):
                continue

            score = 0
            if rank >= 26: score += 4
            elif rank >= 21: score += 2
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
    # Only count starters whose game is today or later (exclude already-pitched
    # starts that haven't cleared Yahoo's banked stats yet)
    roster_ip = 0.0
    for proj in sp_projections:
        try:
            gd = date.fromisoformat(proj.game_date) if proj.game_date else None
        except ValueError:
            gd = None
        if gd is None or gd >= TODAY:
            roster_ip += proj.avg.ip
    total = banked + roster_ip
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
                   score_as_of: str = "",
                   opp_sp_projs: list = None) -> str:
    safe      = [c for c in cat_outcomes if c.action == "SAFE"]
    hedge     = [c for c in cat_outcomes if c.action == "HEDGE"]
    need_help = [c for c in cat_outcomes
                 if c.action in ("NEED_HELP", "STREAM_K", "STREAM_QS")]
    must_starts = [d for d in sp_decisions if d.recommendation == "MUST_START"]

    if must_starts:
        must_names = ", ".join(d.name for d in must_starts)
    elif ip_plan.shortfall > 0:
        # IP floor at risk but no individual start is flagged as must-start
        # (remaining starters collectively cover the gap — start them all).
        all_sp = [d for d in sp_decisions if not d.projection.is_rp]
        must_names = f"Start all {len(all_sp)} remaining SP today to cover IP floor"
    else:
        must_names = None   # IP covered — suppress the must-start line entirely

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
        f"Must-start remaining: {must_names}." if must_names else "",
    ]

    # Opponent threat line — name their highest-K remaining starter
    if opp_sp_projs:
        top = max(opp_sp_projs, key=lambda p: p.avg.k, default=None)
        if top and top.avg.k >= 6:
            parts.append(
                f" {opponent_name}'s {top.name} still starts this week"
                f" (avg {top.avg.k}K, {top.avg.ip}IP) — factor into K/ERA outlook."
            )

    return "".join(p for p in parts if p)
