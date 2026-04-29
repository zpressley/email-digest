"""
weekly_matchup_renderer.py
FBP Email Digest — HTML rendering for the Weekly Matchup Projection block.

Lifted verbatim from weekly_matchup_engine.py. Pure presentation:
no API calls, no projection math, no decision logic. Reads a fully
populated WeekPlan and emits the HTML used by daily_template.html.
"""

from src.data.weekly_matchup_projection import _fmt_ip
from src.data.weekly_matchup_evaluator import (
    ALL_CATS,
    HITTING_CATS,
    PITCHING_CATS,
    IP_MINIMUM,
    WeekPlan,
)


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
        '<tr>'
        '<th>Floor</th><th>Avg</th><th>Exp</th><th>Now</th>'
        '<th class="cat-col">Cat</th>'
        '<th>Now</th><th>Exp</th><th>Avg</th><th>Ceil</th>'
        '</tr>'
    )

    AI = {"SAFE": "✅", "HOLD": "✅", "HEDGE": "⚠️",
          "NEED_HELP": "🚨", "STREAM_K": "🎯", "STREAM_QS": "🎯"}

    CAT_DISPLAY = {
        "HR_hit": "HR",      "K_hit":  "K(bat)",
        "K":      "K(pit)",  "HR":     "HR(pit)",
        "TB_hit": "TB(bat)", "TB":     "TB(pit)",
    }

    for section, cats in [("⚔️ Hitting", HITTING_CATS),
                           ("🎯 Pitching", PITCHING_CATS)]:
        html.append(f'<tr class="section-row"><td colspan="9">{section}</td></tr>')
        for cat in cats:
            c = next((x for x in plan.cat_outcomes if x.cat == cat), None)
            if not c:
                continue
            is_rate = ALL_CATS[cat]["rate"]
            fmt     = ".2f" if is_rate else ".0f"
            icon    = AI.get(c.action, "⚪")
            display = CAT_DISPLAY.get(cat, cat)

            # Your side: Exp column color = action severity
            exp_color = (
                "#137333" if c.action in ("SAFE", "HOLD") else
                "#b45309" if c.action == "HEDGE" else "#c5221f"
            )
            # Now columns: bold green if winning, red if losing
            now_you = "#137333" if c.currently_winning else "#c5221f"
            now_opp = "#c5221f" if c.currently_winning else "#137333"

            html.append(
                f'<tr>'
                f'<td style="color:#9a9a94">{format(c.your_floor_val, fmt)}</td>'
                f'<td style="color:#9a9a94">{format(c.your_avg_val,   fmt)}</td>'
                f'<td style="color:{exp_color};font-weight:600">{format(c.your_exp_val, fmt)}</td>'
                f'<td style="color:{now_you};font-weight:700">{format(c.your_current_val, fmt)}</td>'
                f'<td class="cat-col">{display} {icon}</td>'
                f'<td style="color:{now_opp};font-weight:700">{format(c.opp_current_val, fmt)}</td>'
                f'<td style="color:#9a9a94">{format(c.opp_exp_val,  fmt)}</td>'
                f'<td style="color:#9a9a94">{format(c.opp_avg_val,  fmt)}</td>'
                f'<td style="color:#9a9a94">{format(c.opp_ceil_val, fmt)}</td>'
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
            bad_line  = f'{_fmt_ip(p.bad.ip)}IP / {p.bad.k}K / {p.bad.er}ER / ERA {p.bad.era:.2f} / H9 {p.bad.h9:.2f} / BB9 {p.bad.bb9:.2f}{bad_qs}'
            avg_line  = f'{_fmt_ip(p.avg.ip)}IP / {p.avg.k}K / {p.avg.er}ER / ERA {p.avg.era:.2f} / H9 {p.avg.h9:.2f} / BB9 {p.avg.bb9:.2f}{avg_qs}'
            good_line = f'{_fmt_ip(p.good.ip)}IP / {p.good.k}K / {p.good.er}ER / ERA {p.good.era:.2f} / H9 {p.good.h9:.2f} / BB9 {p.good.bb9:.2f}{good_qs}'
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
