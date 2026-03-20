"""
Claude API client for AI-generated digest sections.
Uses claude-haiku-4-5 — fast and cheap, ~$0.002/day for all three features.
"""
import anthropic
import os

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL = "claude-haiku-4-5-20251001"


def generate_farm_report(prospects: list[dict]) -> str:
    """
    Takes a list of prospect dicts with name, contract, level, and stats.
    Returns a formatted narrative farm system report.
    """
    if not prospects:
        return "No prospect activity to report this week."

    # Build a compact data block for the prompt
    prospect_lines = []
    for p in prospects:
        line = (
            f"- {p['name']} ({p.get('contract','?')} / {p.get('level','MiLB')}): "
            f"{p.get('note', 'No recent stats')}"
        )
        prospect_lines.append(line)

    prospect_text = "\n".join(prospect_lines)

    prompt = f"""You are a fantasy baseball analyst writing a farm system report 
for a dynasty league manager. Write a concise, conversational report covering 
the prospects listed below.

Group them into three tiers if applicable:
🔥 Hot — strong recent performance or call-up imminent
👀 Watch — interesting development worth monitoring  
❄️ Cold — struggling or concerning trend

For each prospect write 1-2 sentences max. Be direct and actionable. 
Reference contract type (PC/DC/BC) where relevant.
Do not include a title or header — just the report body.

Prospects:
{prospect_text}"""

    message = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def generate_baseball_pulse(discord_posts: list[str]) -> str:
    """
    Takes a list of raw Twitter/X posts from the Discord #twitter-feed channel.
    Returns a narrative summary of yesterday's baseball news.
    """
    if not discord_posts:
        return "No baseball pulse data available today."

    posts_text = "\n".join(f"- {post}" for post in discord_posts[:50])

    prompt = f"""You are a fantasy baseball analyst summarizing yesterday's 
baseball news from a curated feed of beat writers and analysts.

Write 3-4 short paragraphs covering:
1. Injury news and roster moves that matter for fantasy
2. Call-up buzz or prospect news
3. Pitcher and hitter performance trends worth noting
4. Any streaming or waiver wire intel from the analyst community

Be direct and conversational. Flag anything especially actionable.
Do not include a title or header — just the summary body.

Posts from yesterday:
{posts_text}"""

    message = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def generate_weekly_recap(
    team_name: str,
    matchup_result: str,
    category_dashboard: list[dict],
) -> str:
    """
    Takes the week's matchup result and category dashboard.
    Returns a narrative weekly recap with strategic recommendations.
    """
    # Build category summary
    cat_lines = []
    for cat in category_dashboard:
        trend = "↑" if cat.get("trend", 0) > 0 else "↓" if cat.get("trend", 0) < 0 else "→"
        cat_lines.append(
            f"- {cat['name']}: rank {cat['my_rank']}/12 {trend} "
            f"(gap to 1st: {cat.get('gap_to_first', '?')})"
        )
    cat_text = "\n".join(cat_lines)

    prompt = f"""You are a fantasy baseball analyst writing a weekly recap 
for the {team_name} dynasty team.

Matchup result this week: {matchup_result}

Category standings:
{cat_text}

Write a 3-4 paragraph weekly recap covering:
1. How the week went — wins, losses, key category performances
2. Where the team is strong vs where it's bleeding categories
3. 2-3 specific actionable recommendations for the coming week
   (free agent targets, roster moves, categories to prioritize)

Be direct, honest, and conversational. Reference specific categories.
Do not include a title or header — just the recap body."""

    message = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text