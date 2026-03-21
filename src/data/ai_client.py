"""
Claude API client for AI-generated digest sections.
Model: claude-haiku-4-5-20251001 — fast, cheap (~$0.002/day for all features)

Three features:
    generate_farm_report()    — prospect narrative from minor league stats
    generate_baseball_pulse() — summarizes Discord Twitter feed channels
    generate_weekly_recap()   — narrative weekly performance summary

Env var: ANTHROPIC_KEY
"""
import os
import anthropic

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_KEY"))
MODEL  = "claude-haiku-4-5-20251001"


def generate_farm_report(prospects: list[dict]) -> str:
    """
    Takes prospect callout dicts from prospect_tracker.py.
    Returns a narrative farm system report grouped by tier.

    Each prospect dict should have:
        name, contract (PC/DC/BC), level, note, positive, type
    """
    if not prospects:
        return ""

    lines = []
    for p in prospects:
        line = (
            f"- {p['name']} "
            f"({p.get('contract', '?')} / {p.get('level', 'MiLB')}): "
            f"{p.get('note', 'No recent stats')}"
        )
        lines.append(line)

    prospect_text = "\n".join(lines)

    prompt = f"""You are a fantasy baseball analyst writing a farm system 
report for a dynasty league manager (team: Whiz Kids, 12-team H2H categories).

Write a concise, conversational scouting report on the prospects below.
Group them into tiers where applicable:
🔥 Hot — strong recent performance or call-up imminent
👀 Watch — interesting development worth monitoring
❄️ Cold — struggling or concerning trend

Rules:
- 1-2 sentences per prospect max
- Be direct and actionable
- Reference contract type (PC/DC/BC) where relevant — BC prospects 
  need Top 100 status to retain their contract, so performance matters
- Flag anyone who looks like a graduation candidate
- Do not include a title or header — just the report body
- If fewer than 3 prospects, skip the tier grouping and just write naturally

Prospects:
{prospect_text}"""

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        print(f"⚠️  AI farm report error: {e}")
        return ""


def generate_baseball_pulse(feed_text: str) -> str:
    """
    Takes formatted Discord feed text from discord_reader.get_posts_as_text().
    Returns a narrative summary of yesterday's baseball news.

    Feed text is grouped by channel:
        [#trade-rumors] posts...
        [#prospects] posts...
        etc.
    """
    if not feed_text or len(feed_text.strip()) < 50:
        return ""

    prompt = f"""You are a fantasy baseball analyst summarizing yesterday's 
baseball news for a dynasty league manager (12-team H2H categories league).

The feed below comes from curated Twitter/X accounts routed to Discord channels:
- #trade-rumors: transaction news and roster moves
- #mlb-official: official MLB and Pipeline news
- #yard: home run and power highlights  
- #twitter-dump: general baseball analysis and commentary

Write 3-4 short paragraphs covering the most relevant items:
1. Injury news and roster moves that impact fantasy rosters
2. Call-up buzz or prospect promotions worth acting on
3. Pitcher or hitter trends and performance notes
4. Any streaming, waiver wire, or fantasy-specific intel

Rules:
- Be direct and actionable — this is a fantasy manager's morning briefing
- This league has a 1-day roster lag — pickups made today 
  are not active until tomorrow. Never suggest same-day adds.
  Always frame urgency as "add today to be rostered tomorrow"
  or "add by [day] to be active for [day+1 start]"
- Skip anything that is purely entertainment or not fantasy-relevant
- Do not include a title or header — just the summary body
- If the feed is mostly noise with nothing actionable, say so briefly

Feed from yesterday:
{feed_text}"""

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        print(f"⚠️  AI baseball pulse error: {e}")
        return ""


def generate_weekly_recap(
    matchup_result: str,
    category_dashboard: list[dict],
    target_categories: list[dict],
) -> str:
    """
    Takes the week's matchup result and category dashboard from
    category_standings.py. Returns a narrative weekly recap with
    strategic recommendations for the coming week.
    """
    if not category_dashboard:
        return ""

    # Format category table for prompt
    cat_lines = []
    for cat in category_dashboard:
        trend = (
            "↑" if cat.get("trend", 0) > 0
            else "↓" if cat.get("trend", 0) < 0
            else "→"
        )
        target_flag = " 🎯" if cat.get("is_target") else ""
        cat_lines.append(
            f"- {cat['name']}{target_flag}: "
            f"rank {cat.get('my_rank', '?')}/12 {trend} | "
            f"gap to 1st: {cat.get('gap_to_first', '?')} | "
            f"gap to last: {cat.get('gap_to_last', '?')}"
        )
    cat_text = "\n".join(cat_lines)

    # Format target categories
    target_lines = []
    for cat in (target_categories or []):
        target_lines.append(
            f"- {cat.get('name')}: need {cat.get('gap', '?')} to move up. "
            f"FA targets: {', '.join(cat.get('fa_targets', []))}"
        )
    target_text = "\n".join(target_lines) if target_lines else "None identified"

    prompt = f"""You are a fantasy baseball analyst writing a weekly recap 
for the Weekend Warriors dynasty team (12-team H2H categories league, 20 categories).

Matchup result this week: {matchup_result}

Category standings (🎯 = identified target category):
{cat_text}

Recommended target categories for next week:
{target_text}

Write a 3-4 paragraph weekly recap:
1. How the week went — wins, losses, which categories were won/lost and why
2. Honest assessment of where the roster is strong vs where it's bleeding
3. 2-3 specific actionable moves for the coming week with clear reasoning
   (reference the target categories and FA targets above)
4. One forward-looking note — what to watch for in the next 2 weeks

Rules:
- Be direct and honest — don't sugarcoat a bad week
- Reference specific category names (ERA, SB, HR, etc.)
- Keep recommendations actionable, not generic
- Do not include a title or header — just the recap body"""

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        print(f"⚠️  AI weekly recap error: {e}")
        return ""