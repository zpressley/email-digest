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
report for a dynasty league manager (team: Weekend Warriors, 12-team H2H categories).

Write a concise, conversational scouting report on the prospects below.
Group them into tiers where applicable:
Hot — strong recent performance or call-up imminent
Watch — interesting development worth monitoring
Cold — struggling or concerning trend

Rules:
- 1-2 sentences per prospect max
- Be direct and actionable
- Plain text only. No markdown, no bold, no asterisks, no bullet points.
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
        [#mlb-official] posts...
        etc.
    """
    if not feed_text or len(feed_text.strip()) < 50:
        return ""

    prompt = f"""You are a fantasy baseball analyst summarizing yesterday's
baseball news for the Weekend Warriors dynasty team manager
(12-team H2H categories league, 20 categories).

The feed below is categorized by account type:
- TRANSACTIONS: confirmed roster moves, injuries, IL placements, call-ups
- PROSPECTS: minor league stats, scouting reports, prospect rankings
- STATCAST: analytics, pitch data, Statcast metrics, chart insights
- VIBES: commentary, highlights, general baseball stories

Write 4 short paragraphs, one per category that has meaningful content.
Plain text only. No markdown, no bold, no asterisks, no bullet points.

Ignore completely:
- Promotional posts, sweepstakes, giveaways, sponsored content
- Context-free reactions with no baseball intel ("well this didn't age well")
- Newsletter titles or podcast announcements with no actual content
- Ring ceremonies, award shows, walk-up songs, bobblehead giveaways
- Game celebration posts with no fantasy-relevant information
- Any post where a player is referenced without using their full name

Rules:
- Never reference a player without using their full name
- Never invent or guess details not clearly stated in the feed
- If details about a player are unclear or missing, skip that item
- This league has a mandatory 1-day roster lag — pickups today are
  active tomorrow only. Frame urgency as "add today, active tomorrow"
- Focus on what is actionable for a dynasty fantasy manager
- If a category has nothing meaningful, skip that paragraph entirely
- Do not include a title or header — just the summary body

Feed:
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
    Takes the week's matchup result and category dashboard.
    Returns a narrative weekly recap with strategic recommendations.
    """
    if not category_dashboard:
        return ""

    cat_lines = []
    for cat in category_dashboard:
        trend = (
            "up" if cat.get("trend", 0) > 0
            else "down" if cat.get("trend", 0) < 0
            else "flat"
        )
        target_flag = " (target)" if cat.get("is_target") else ""
        cat_lines.append(
            f"- {cat['name']}{target_flag}: "
            f"rank {cat.get('my_rank', '?')}/12 {trend} | "
            f"gap to 1st: {cat.get('gap_to_first', '?')} | "
            f"gap to last: {cat.get('gap_to_last', '?')}"
        )
    cat_text = "\n".join(cat_lines)

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

Category standings (marked as target where improvement is needed):
{cat_text}

Recommended target categories for next week:
{target_text}

Write a 3-4 paragraph weekly recap. Plain text only — no markdown, no bold,
no asterisks, no bullet points. Just clean paragraph breaks.

Cover:
1. How the week went — wins, losses, which categories were won or lost and why
2. Honest assessment of where the roster is strong vs where it is bleeding
3. Two or three specific actionable moves for the coming week with clear reasoning
4. One forward-looking note — what to watch for in the next two weeks

Rules:
- Plain text only. No bold, no italic, no bullet points, no headers.
- Be direct and honest — do not sugarcoat a bad week
- Reference specific category names like ERA, SB, HR
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