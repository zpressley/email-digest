"""
Claude API client for AI-generated digest sections.

Uses claude-haiku-4-5-20251001 for all generations.
API key from ANTHROPIC_KEY environment variable.

Sections generated:
    generate_farm_report()      — WAR prospect narrative
    generate_baseball_pulse()   — personalized daily news summary
    generate_weekly_recap()     — weekly performance summary
"""
import os
import json
import anthropic

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
MODEL         = "claude-haiku-4-5-20251001"

# WAR roster context injected into pulse prompt
# These are the players the AI should watch for specifically
MY_TEAM_ABBR    = os.getenv("MY_TEAM_ABBR", "WAR")
MY_TEAM_NAME    = "Weekend Warriors"

# Load my roster player names for the pulse prompt
def _load_my_roster_names() -> list[str]:
    """Load WAR roster player names from combined_players.json."""
    try:
        path = os.getenv("COMBINED_PLAYERS_PATH", "data/combined_players.json")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            players = json.load(f)
        names = [
            p["name"] for p in players
            if p.get("manager") == MY_TEAM_ABBR
            and p.get("player_type") == "MLB"
            and p.get("name")
        ]
        return names[:30]  # cap at 30 to keep prompt size reasonable
    except Exception:
        return []


def _load_my_prospect_names() -> list[str]:
    """Load WAR prospect names from combined_players.json."""
    try:
        path = os.getenv("COMBINED_PLAYERS_PATH", "data/combined_players.json")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            players = json.load(f)
        names = [
            p["name"] for p in players
            if p.get("manager") == MY_TEAM_ABBR
            and p.get("player_type") == "Farm"
            and p.get("name")
        ]
        return names[:25]
    except Exception:
        return []


def generate_baseball_pulse(feed_text: str) -> str:
    """
    Generate a personalized baseball pulse summary from the Discord feed.

    Structure (priority order):
        1. My Team News     — anything touching WAR roster players
        2. My Prospects     — anything touching WAR farm system players
        3. Around the League — fantasy-relevant transactions only
        4. Baseball Today   — highlights, stories, vibes (brief)

    Sections 1 and 2 are only included if there is relevant content.
    Transactions are only included if they are fantasy-relevant
    (injuries, IL moves, call-ups, significant role changes).
    Minor league signings and non-roster moves are ignored.
    """
    if not feed_text or not ANTHROPIC_KEY:
        return ""

    my_roster   = _load_my_roster_names()
    my_prospects = _load_my_prospect_names()

    roster_list   = ", ".join(my_roster)   if my_roster   else "not available"
    prospect_list = ", ".join(my_prospects) if my_prospects else "not available"

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""You are a personal fantasy baseball analyst writing a daily briefing
for the manager of the Weekend Warriors (WAR), a dynasty H2H categories team
in a 12-team league.

MY ROSTER PLAYERS:
{roster_list}

MY PROSPECTS (farm system):
{prospect_list}

Write a daily baseball pulse in exactly 4 sections using these headers:
MY TEAM, MY PROSPECTS, AROUND THE LEAGUE, BASEBALL TODAY

Rules for each section:

MY TEAM:
- Only include players from My Roster Players list above
- Cover injuries, IL moves, lineup changes, performance news, anything
  that affects whether to start, sit, or drop a player
- If none of my roster players appear in the feed, write one sentence
  saying there is no relevant news for my roster today
- 2-4 sentences maximum

MY PROSPECTS:
- Only include players from My Prospects list above
- Cover call-ups, demotion, performance news, injury updates
- If none of my prospects appear in the feed, write one sentence
  saying no prospect news today
- 2-3 sentences maximum

AROUND THE LEAGUE:
- Only include transactions that are fantasy-relevant:
  injuries, IL placements, call-ups, significant role changes,
  closers losing jobs, lineup changes affecting counting stats
- Ignore completely: minor league signings, non-roster invitees,
  outright assignments with no fantasy impact, contract extensions
  for non-fantasy-relevant players, retirements of players not
  in fantasy leagues, award ceremonies, front office hires
- 3-4 sentences maximum
- Frame everything in terms of fantasy impact

BASEBALL TODAY:
- Highlights, notable performances, interesting stories
- Keep it brief and engaging — 2-3 sentences only
- This section should never be cut off so keep it short

Formatting rules:
- Plain text only — no markdown, no asterisks, no bullet points
- Each section starts with the header on its own line in ALL CAPS
- Then a blank line
- Then the paragraph
- Never invent details not in the feed
- Never use a player's name without their full name
- League has 1-day roster lag — pickups today are active tomorrow

FEED:
{feed_text}"""

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  Baseball pulse generation error: {e}")
        return ""


def generate_farm_report(prospect_callouts: list[dict]) -> str:
    """
    Generate a narrative farm system report for WAR prospects.
    Only called when prospect_callouts has actual data.
    """
    if not prospect_callouts or not ANTHROPIC_KEY:
        return ""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    callout_text = "\n".join([
        f"- {p['name']} ({p.get('level','')}, {p.get('contract','')}): {p.get('note','')}"
        for p in prospect_callouts
        if p.get("name")
    ])

    prompt = f"""You are a farm system analyst for the Weekend Warriors dynasty
fantasy baseball team. Write a brief 2-3 sentence narrative about the
following prospect activity from yesterday. Be specific about each player
mentioned. Use plain text only — no markdown, no bullets, no bold.
Focus on fantasy implications: proximity to call-up, development signals,
contract status implications.

Prospect activity:
{callout_text}"""

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  Farm report generation error: {e}")
        return ""


def generate_weekly_recap(context: dict) -> str:
    """
    Generate a weekly performance recap for the Sunday digest.
    Covers matchup result, category wins/losses, and roster notes.
    """
    if not ANTHROPIC_KEY:
        return ""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""You are a fantasy baseball analyst writing a weekly recap
for the Weekend Warriors. Write 3-4 sentences covering:
- How the matchup went this week (wins/losses by category if available)
- Standout performers on the roster
- Any notable injuries or roster moves that affected the week
- One forward-looking note about the coming week

Use plain text only. Be direct and analytical.

Context:
{json.dumps(context, indent=2, default=str)}"""

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  Weekly recap generation error: {e}")
        return ""