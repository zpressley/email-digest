"""
Offline smoke-test for weekly_matchup_engine.
Mocks all external API calls — no Yahoo/MLB credentials needed.
Run from repo root: PYTHONPATH=. python tests/test_engine_offline.py
"""
import sys
import traceback
from datetime import date

# ── Patch external clients before importing engine ────────────────────────────
import unittest.mock as mock

# Stub out requests so nothing hits the network
import requests
requests.get = mock.MagicMock(side_effect=RuntimeError("network disabled in test"))

# ── Now safe to import engine ─────────────────────────────────────────────────
from src.data.weekly_matchup_engine import (
    AppearanceLog,
    AppOutcome,
    PitcherProjection,
    CatOutcome,
    TeamWeekLine,
    IPPlan,
    WeekPlan,
    StartDecision,
    StreamerRec,
    HITTING_CATS,
    PITCHING_CATS,
    ALL_CATS,
    IP_MINIMUM,
    TODAY,
    _fmt_ip,
    get_last_n_starts,
    _build_reasoning,
    _fallback_projection,
    build_projection,
    evaluate_categories,
    build_hitting_line,
    aggregate_pitching_line,
    build_ip_plan,
    build_summary,
    render_scorecard,
    fetch_tb_rate_and_role,
)

PASS = "✅"
FAIL = "❌"
results = []


def check(name, expr):
    try:
        assert expr, f"assertion failed"
        results.append((PASS, name))
        print(f"{PASS} {name}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"{FAIL} {name}: {e}")
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# 1. _fmt_ip
# ─────────────────────────────────────────────────────────────────────────────
check("_fmt_ip whole innings",      _fmt_ip(6.0)   == "6.0")
check("_fmt_ip one-third",          _fmt_ip(6.333) == "6.1")
check("_fmt_ip two-thirds",         _fmt_ip(6.667) == "6.2")
check("_fmt_ip blow-up (0.667)",    _fmt_ip(0.667) == "0.2")


# ─────────────────────────────────────────────────────────────────────────────
# 2. get_last_n_starts — no IP filter
# ─────────────────────────────────────────────────────────────────────────────
logs = [
    AppearanceLog(ip=0.667, k=0, er=7, hr=1, h=5, bb=2, tb=0.97, qs=0, date="2026-04-20", season=2026, weight=1.0),
    AppearanceLog(ip=6.333, k=8, er=1, hr=0, h=4, bb=1, tb=9.18, qs=1, date="2026-04-14", season=2026, weight=0.9),
    AppearanceLog(ip=5.0,   k=5, er=3, hr=1, h=6, bb=2, tb=7.25, qs=0, date="2026-04-08", season=2026, weight=0.8),
    AppearanceLog(ip=7.0,   k=9, er=0, hr=0, h=3, bb=1, tb=10.15,qs=1, date="2026-04-02", season=2026, weight=0.7),
]
last3 = get_last_n_starts(logs, 3)
check("get_last_n_starts returns 3",        len(last3) == 3)
check("get_last_n_starts newest first",     last3[0].date == "2026-04-20")
check("get_last_n_starts keeps blow-up",    last3[0].ip == 0.667)  # was filtered out before
check("get_last_n_starts no IP floor",      min(lg.ip for lg in last3) < 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# 3. _build_reasoning — blow-up detection
# ─────────────────────────────────────────────────────────────────────────────
bad  = AppOutcome("bad",     1.5, 2, 5, 1, 7, 3, 2.2, 0, 1, 30.0, 12.0, 42.0, 18.0)
avg  = AppOutcome("average", 5.1, 6, 3, 1, 7, 2, 7.4, 0, 1,  5.3, 10.6, 12.4,  3.5)
good = AppOutcome("good",    6.5, 9, 1, 0, 4, 1, 9.4, 1, 1,  1.4, 12.5,  5.5,  1.4)

proj_blowup = PitcherProjection(
    name="Test Pitcher", team="MIL", opponent="NYY",
    opp_offense_rank=4, good=good, avg=avg, bad=bad,
    log_count=len(logs), data_quality="OK", is_rp=False, expected_apps=1.0,
    last_3=last3,
)

cat_outcomes_mock = []  # empty — no category stakes
reasoning = _build_reasoning(False, proj_blowup, cat_outcomes_mock)
check("_build_reasoning returns string",        isinstance(reasoning, str) and len(reasoning) > 10)
check("_build_reasoning detects blow-up",       "blow-up" in reasoning or "blowup" in reasoning.lower())
check("_build_reasoning includes date",         "2026-04-20" in reasoning)
check("_build_reasoning includes ER count",     "7" in reasoning)
check("_build_reasoning includes matchup",      "NYY" in reasoning or "top-5" in reasoning)

# IP floor flag
reasoning_ip = _build_reasoning(True, proj_blowup, cat_outcomes_mock)
check("_build_reasoning IP floor mention",      "IP" in reasoning_ip and "floor" in reasoning_ip.lower())


# ─────────────────────────────────────────────────────────────────────────────
# 4. _fallback_projection + build_projection
# ─────────────────────────────────────────────────────────────────────────────
fb = _fallback_projection("Fake Pitcher", "NYY", "BOS", 15, is_rp=False, expected_apps=1.0)
check("fallback projection SP",      not fb.is_rp)
check("fallback projection avg ip",  fb.avg.ip > 4.0)
check("fallback projection bad ip",  fb.bad.ip >= 1.5)

proj_built = build_projection("Real Pitcher", "MIL", "CHC", 20, logs, is_rp=False)
check("build_projection not RP",     not proj_built.is_rp)
check("build_projection avg ip > 0", proj_built.avg.ip > 0)
check("build_projection bad ip > 0", proj_built.bad.ip > 0)
check("build_projection data qual",  proj_built.data_quality in ("STRONG","OK","THIN","FALLBACK"))


# ─────────────────────────────────────────────────────────────────────────────
# 5. evaluate_categories with new 8-param signature
# ─────────────────────────────────────────────────────────────────────────────
def _make_pitch_line(scenario):
    return aggregate_pitching_line([proj_built], scenario, {})

def _make_hit_line(bk, remaining, scenario):
    rolling = {
        "R":10,"H":28,"HR_hit":3,"RBI":12,"SB":2,"BB":8,"K_hit":18,"TB_hit":45,
        "AVG":0.260,"OPS":0.740,"days_in_window":21,
        "banked_R":bk,"banked_H":bk*3,"banked_HR_hit":0,"banked_RBI":bk,
        "banked_SB":0,"banked_BB":bk,"banked_K_hit":bk*2,"banked_TB_hit":bk*5,
    }
    return build_hitting_line(rolling, remaining, scenario)

your_floor_line  = TeamWeekLine("floor",        hitting=_make_hit_line(3,3,"poor"),    pitching=_make_pitch_line("bad"))
your_exp_line    = TeamWeekLine("avg",           hitting=_make_hit_line(3,3,"average"), pitching=_make_pitch_line("avg"))
your_base_line   = TeamWeekLine("avg_baseline",  hitting=_make_hit_line(0,7,"average"), pitching=_make_pitch_line("avg"))
opp_ceiling_line = TeamWeekLine("ceiling",       hitting=_make_hit_line(4,3,"good"),    pitching=_make_pitch_line("good"))
opp_exp_line     = TeamWeekLine("avg",           hitting=_make_hit_line(4,3,"average"), pitching=_make_pitch_line("avg"))
opp_base_line    = TeamWeekLine("avg_baseline",  hitting=_make_hit_line(0,7,"average"), pitching=_make_pitch_line("avg"))

my_banked  = {"R":5,"H":14,"HR_hit":1,"RBI":5,"SB":0,"BB":3,"K_hit":9,"TB":22,"AVG":0.262,"OPS":0.745,"IP":12.0,"K":8,"ER":3,"HR":0,"APP":4,"QS":1,"H_allowed":10,"BB_allowed":3}
opp_banked = {"R":4,"H":12,"HR_hit":0,"RBI":4,"SB":1,"BB":2,"K_hit":8,"TB":18,"AVG":0.250,"OPS":0.710,"IP":11.0,"K":7,"ER":4,"HR":1,"APP":3,"QS":0,"H_allowed":12,"BB_allowed":4}

cat_outcomes = evaluate_categories(
    your_floor=your_floor_line,
    your_exp=your_exp_line,
    your_avg_baseline=your_base_line,
    opp_ceiling=opp_ceiling_line,
    opp_exp=opp_exp_line,
    opp_avg_baseline=opp_base_line,
    current_mine=my_banked,
    current_opp=opp_banked,
)
# ALL_CATS now has 20 unique keys — TB_hit (batting) and TB (pitching) are distinct
check("evaluate_categories returns 20 cats", len(cat_outcomes) == 20)
check("ALL_CATS has 20 unique keys",         len(ALL_CATS) == 20)
check("CatOutcome has your_exp_val",             hasattr(cat_outcomes[0], "your_exp_val"))
check("CatOutcome has your_current_val",         hasattr(cat_outcomes[0], "your_current_val"))
check("CatOutcome has opp_exp_val",              hasattr(cat_outcomes[0], "opp_exp_val"))
check("CatOutcome has opp_current_val",          hasattr(cat_outcomes[0], "opp_current_val"))
check("CatOutcome action is valid",              cat_outcomes[0].action in ("SAFE","HEDGE","HOLD","NEED_HELP","STREAM_K","STREAM_QS"))
r_cat = next(c for c in cat_outcomes if c.cat == "R")
check("R cat your_current_val correct",          r_cat.your_current_val == 5)
check("R cat opp_current_val correct",           r_cat.opp_current_val  == 4)


# ─────────────────────────────────────────────────────────────────────────────
# 6. build_ip_plan with game_date filtering
# ─────────────────────────────────────────────────────────────────────────────
proj_today     = PitcherProjection("Today SP",    "MIL","CHC",15,good,avg,bad,10,"OK",False,1.0, game_date=TODAY.isoformat())
proj_tomorrow  = PitcherProjection("Tomorrow SP", "MIL","PIT",18,good,avg,bad,10,"OK",False,1.0, game_date=(TODAY.replace(day=TODAY.day+1)).isoformat() if TODAY.day < 28 else TODAY.isoformat())
proj_yesterday = PitcherProjection("Yesterday SP","MIL","STL",12,good,avg,bad,10,"OK",False,1.0, game_date="2026-04-01")  # definitely past

ip_plan = build_ip_plan(10.0, [proj_today, proj_tomorrow, proj_yesterday], [])
check("build_ip_plan includes today",         ip_plan.roster_projected >= proj_today.avg.ip)
check("build_ip_plan excludes past start",    ip_plan.roster_projected < proj_today.avg.ip + proj_tomorrow.avg.ip + proj_yesterday.avg.ip)


# ─────────────────────────────────────────────────────────────────────────────
# 7. build_summary
# ─────────────────────────────────────────────────────────────────────────────
sd = StartDecision(
    name="Test Pitcher", team="MIL", opponent="NYY", opp_offense_rank=4,
    recommendation="START", confidence="HIGH",
    reasoning="Test reasoning.", ip_floor_flag=False, projection=proj_built,
)
opp_sp = [proj_built]
summary = build_summary(
    cat_outcomes, ip_plan, [sd],
    current_score_you=7, current_score_opp=6,
    opponent_name="B2J", score_as_of="Wed Apr 23",
    opp_sp_projs=opp_sp,
)
check("build_summary returns non-empty string", len(summary) > 20)
check("build_summary includes score",           "7" in summary or "B2J" in summary)


# ─────────────────────────────────────────────────────────────────────────────
# 8. render_scorecard — full HTML output
# ─────────────────────────────────────────────────────────────────────────────
plan = WeekPlan(
    your_floor=your_floor_line, your_avg=your_exp_line,
    opp_ceiling=opp_ceiling_line, opp_avg=opp_exp_line,
    cat_outcomes=cat_outcomes,
    start_decisions=[sd],
    streamers=[],
    ip_plan=ip_plan,
    summary=summary,
    opponent_name="B2J",
    current_score_you=7, current_score_opp=6,
    score_as_of="Wed Apr 23",
    bullpen_summary="Bullpen projects 3.5 apps this week.",
)
html = render_scorecard(plan)
check("render_scorecard returns HTML string",       isinstance(html, str) and len(html) > 500)
check("render_scorecard has 9-col header",          "<th>Floor</th>" in html and "<th>Ceil</th>" in html)
check("render_scorecard has cat-col",               'class="cat-col"' in html)
check("render_scorecard has Exp column",            "<th>Exp</th>" in html)
check("render_scorecard no colspan=8 (old)",        'colspan="8"' not in html)
check("render_scorecard has colspan=9",             'colspan="9"' in html)
check("render_scorecard has SP card",               "Test Pitcher" in html)
check("render_scorecard has bullpen bar",           "Bullpen" in html)
check("render_scorecard no raw float IP",           "3.3333" not in html and "6.6666" not in html)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed out of {len(results)} checks")
if failed:
    print("FAILED checks:")
    for r in results:
        if r[0] == FAIL:
            print(f"  {r[1]}")
    sys.exit(1)
else:
    print("All checks passed.")
