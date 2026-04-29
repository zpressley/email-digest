"""
weekly_matchup_engine.py
FBP Email Digest — Weekly Matchup Projection Engine (2026)

Thin orchestrator. Glues together three sibling modules:
  - weekly_matchup_projection : pitcher projection (logs, bootstrap, fallback)
  - weekly_matchup_evaluator  : categories, decisions, streamers, IP plan, summary
  - weekly_matchup_renderer   : HTML

Public API:
  get_weekly_matchup_section(yahoo_client, mlb_client,
                              team_offense_ranker, combined_players)

Stat key naming (avoids Yahoo ID collisions):
  K       = pitching strikeouts
  K_hit   = batting strikeouts
  HR      = pitching HR allowed
  HR_hit  = batting home runs
  TB      = pitching total bases allowed
  TB_hit  = batting total bases
  ERA, K/9, H/9, BB/9 derived from components — not fetched directly

Backward-compat re-exports below preserve `from src.data.weekly_matchup_engine
import ...` import paths used by tests/scripts.
"""

from datetime import date, timedelta

# ── Re-exports for backward compatibility ─────────────────────────────────────
# tests/test_engine_offline.py and other call sites import these names
# directly from this module. Keeping the surface stable lets the split be
# a pure structural move \u2014 no behavior or call-site changes.

from src.data.weekly_matchup_projection import (   # noqa: F401
    SEASONS,
    SEASON_MULT,
    DECAY_HALFLIFE,
    TODAY,
    CURRENT_SEASON,
    PITCHER_LOG_CACHE_DIR,
    AppearanceLog,
    AppOutcome,
    PitcherProjection,
    _parse_ip,
    _fmt_ip,
    _recency_weight,
    _offense_rank_adj,
    _cache_path,
    _load_log_from_cache,
    _save_log_to_cache,
    fetch_tb_rate_and_role,
    fetch_rp_availability,
    fetch_appearance_logs,
    get_last_n_starts,
    _to_outcome,
    build_projection,
    _fallback_projection,
    project_pitcher,
)

from src.data.weekly_matchup_evaluator import (   # noqa: F401
    IP_MINIMUM,
    HITTING_CATS,
    PITCHING_CATS,
    ALL_CATS,
    TeamWeekLine,
    CatOutcome,
    StartDecision,
    StreamerRec,
    IPPlan,
    WeekPlan,
    aggregate_pitching_line,
    build_hitting_line,
    evaluate_categories,
    _build_reasoning,
    make_start_decision,
    find_streamers,
    build_ip_plan,
    build_summary,
)

from src.data.weekly_matchup_renderer import render_scorecard   # noqa: F401


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

        def project_rotation(pitchers, remaining_games, is_opponent=False):
            projs = []
            for p in pitchers:
                mlb_id   = mlb_id_map.get(p["name"])
                opp_rank = team_offense_ranker.get_offense_rank(p.get("opponent", ""))
                p["opp_rank"] = opp_rank

                if not mlb_id:
                    if not is_opponent:
                        continue  # own pitchers must have mlb_id — skip is correct
                    # Opponent pitcher not in combined_players — use league-average fallback
                    pos    = p.get("position", "")
                    is_rp  = "SP" not in pos and any(x in pos for x in ("RP", "MR", "CL", "P"))
                    e_apps = max(0.3, remaining_games * 0.25) if is_rp else 1.0
                    proj   = _fallback_projection(
                        p["name"], p.get("team", ""), p.get("opponent", "UNK"),
                        opp_rank, is_rp, e_apps,
                    )
                    projs.append(proj)
                    continue

                proj = project_pitcher(p, mlb_id, remaining_games, mlb_client)
                if proj:
                    projs.append(proj)
            return projs

        my_projs  = project_rotation(my_pitchers,  my_remaining_games, is_opponent=False)
        opp_projs = project_rotation(opp_pitchers, opp_remaining_games, is_opponent=True)

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
        your_avg    = my_lines["average"]   # = banked + remaining (used as your_exp)
        opp_ceiling = opp_lines["ceiling"]
        opp_avg     = opp_lines["average"]  # = opp banked + remaining (used as opp_exp)

        # ── Season-baseline lines (rate × 7, no banked) — for Avg display column
        def _zero_banked(rolling: dict) -> dict:
            return {k: (0 if k.startswith("banked_") else v) for k, v in rolling.items()}

        my_hit_base  = build_hitting_line(_zero_banked(my_rolling),  7, "average")
        opp_hit_base = build_hitting_line(_zero_banked(opp_rolling), 7, "average")
        my_pitch_base  = aggregate_pitching_line(my_projs,  "avg", {})
        opp_pitch_base = aggregate_pitching_line(opp_projs, "avg", {})
        my_avg_baseline  = TeamWeekLine("avg_baseline", hitting=my_hit_base,  pitching=my_pitch_base)
        opp_avg_baseline = TeamWeekLine("avg_baseline", hitting=opp_hit_base, pitching=opp_pitch_base)

        # Populate game_date on SP projections for build_ip_plan filtering
        for proj in my_projs:
            if not proj.is_rp:
                proj.game_date = pitcher_game_dates.get(proj.name, "")

        cat_outcomes = evaluate_categories(
            your_floor=your_floor,
            your_exp=your_avg,
            your_avg_baseline=my_avg_baseline,
            opp_ceiling=opp_ceiling,
            opp_exp=opp_avg,
            opp_avg_baseline=opp_avg_baseline,
            current_mine=my_banked,
            current_opp=opp_banked,
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
        opp_sp_projs_only = [p for p in opp_projs if not p.is_rp]
        summary = build_summary(
            cat_outcomes, ip_plan, sp_decisions_only,
            current_score_you=current_score_you,
            current_score_opp=current_score_opp,
            opponent_name=opponent_name,
            score_as_of=score_as_of,
            opp_sp_projs=opp_sp_projs_only,
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
