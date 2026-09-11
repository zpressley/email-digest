"""
Claude API client for AI-generated digest sections.

API key from ANTHROPIC_KEY environment variable.

Sections generated:
    generate_offseason_brief()  — offseason: prospect + 2027 outlook analysis
                                  of the Discord Twitter feed (claude-opus-5,
                                  the core of the offseason digest)
    generate_baseball_pulse()   — in-season daily news summary (Haiku)
    generate_farm_report()      — in-season WAR prospect narrative (Haiku)
"""
import os
import json
import re
import anthropic

ANTHROPIC_KEY   = os.getenv("ANTHROPIC_KEY") or os.getenv("ANTHROPIC_API_KEY")
MODEL           = "claude-haiku-4-5-20251001"
OFFSEASON_MODEL = "claude-opus-5"

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
        from src.data.league_rosters import manager_matches
        names = [
            p["name"] for p in players
            if manager_matches(p, MY_TEAM_ABBR)
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
        from src.data.league_rosters import manager_matches
        names = [
            p["name"] for p in players
            if manager_matches(p, MY_TEAM_ABBR)
            and p.get("player_type") == "Farm"
            and p.get("name")
        ]
        return names[:25]
    except Exception:
        return []


# Section headers the offseason brief must use, in display order, with the
# CSS label class the template applies to each.
OFFSEASON_SECTIONS = [
    ("MY ROSTER",         "my-team"),
    ("MY PROSPECTS",      "prospects"),
    ("TRANSACTIONS",      "league"),
    ("PROSPECT PIPELINE", "prospects"),
    ("2027 OUTLOOK",      "baseball"),
]


def generate_offseason_brief(feed_text: str) -> list[dict]:
    """
    Offseason digest core: analyze the Discord Twitter feed for prospect
    news and 2027 fantasy implications, personalized to the WAR roster
    and farm system.

    Returns a list of sections: [{"label", "css_class", "body"}, ...].
    Empty list when there is no feed or no API key.
    """
    print(f"  🥶 generate_offseason_brief: feed_len={len(feed_text or '')} "
          f"key_present={bool(ANTHROPIC_KEY)}")
    if not feed_text:
        print("  ⚠️  Offseason brief: no feed text — returning empty")
        return []
    if not ANTHROPIC_KEY:
        print("  ❌ Offseason brief: ANTHROPIC_KEY not set — returning empty")
        return []

    my_roster    = _load_my_roster_names()
    my_prospects = _load_my_prospect_names()
    roster_list   = ", ".join(my_roster)    if my_roster    else "not available"
    prospect_list = ", ".join(my_prospects) if my_prospects else "not available"

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""You are the offseason analyst for the Weekend Warriors (WAR), a
dynasty team in a 12-team H2H categories fantasy baseball league (FBP).
The season is over. Your job is to read today's curated Twitter/X feed
and produce an offseason briefing focused on two things: prospect
development and 2027 fantasy value.

MY ROSTER (dynasty holds under evaluation for 2027):
{roster_list}

MY PROSPECTS (farm system):
{prospect_list}

Write the briefing in exactly 5 sections using these headers:
MY ROSTER, MY PROSPECTS, TRANSACTIONS, PROSPECT PIPELINE, 2027 OUTLOOK

MY ROSTER:
- Only players from My Roster above
- Offseason news that changes their 2027 outlook: surgeries and recovery
  timelines, trades or signings that change their team/park/role,
  velocity or swing-change reports, age-curve red flags
- Frame each item as: what happened, and what it means for whether to
  keep, trade, or cut for 2027
- If nothing relevant, one sentence saying so
- 3-5 sentences maximum

MY PROSPECTS:
- Only players from My Prospects above
- Ranking movement, AFL/winter ball performance, 40-man additions,
  Rule 5 exposure, spring invite chatter, injury updates
- If nothing relevant, one sentence saying so
- 2-4 sentences maximum

TRANSACTIONS:
- League-wide trades, signings, non-tenders, and role changes with real
  2027 fantasy impact (park changes, playing time, closer jobs,
  rotation spots)
- Always name the player and say which direction their 2027 value moves
- Skip minor-league deals and non-fantasy-relevant moves
- 3-5 sentences maximum

PROSPECT PIPELINE:
- League-wide prospect news beyond my farm: top-100 ranking movement,
  breakout AFL performances, notable trades of prospects, posting news
  from NPB/KBO, draft or international signing buzz
- This feeds dynasty pickups — flag anyone who sounds like a target
- 3-5 sentences maximum

2027 OUTLOOK:
- The feed's most interesting forward-looking takes: breakout and
  sleeper chatter, regression warnings, projection-system notes
- Attribute takes to their source handle when it matters
- 2-4 sentences maximum

Formatting rules:
- Plain text only — no markdown, no asterisks, no bullet points
- Each section: the header on its own line in ALL CAPS, then a blank
  line, then the paragraph
- Never invent details not in the feed; it is fine for a section to say
  there is no news today
- Always use players' full names

FEED:
{feed_text}"""

    print(f"  🥶 Offseason brief: sending prompt ({len(prompt)} chars) "
          f"to {OFFSEASON_MODEL}")
    try:
        message = client.messages.create(
            model=OFFSEASON_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        out = next(
            (b.text for b in message.content if b.type == "text"), ""
        ).strip()
        print(f"  ✅ Offseason brief: received {len(out)} chars")
        return parse_offseason_sections(out)
    except Exception as e:
        print(f"  ❌ Offseason brief generation error: {type(e).__name__}: {e!r}")
        return []


def parse_offseason_sections(text: str) -> list[dict]:
    """
    Parse 'HEADER\\n\\nbody' chunks into template-ready section dicts.
    Tolerant of headers followed directly by their body on the next line.
    Unknown headers still render, with the default label style.
    """
    if not text:
        return []

    known = {label: css for label, css in OFFSEASON_SECTIONS}
    header_re = re.compile(
        r"^(" + "|".join(re.escape(label) for label, _ in OFFSEASON_SECTIONS) + r")\s*$",
        re.MULTILINE,
    )

    sections = []
    matches = list(header_re.finditer(text))
    for i, m in enumerate(matches):
        label = m.group(1)
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body  = " ".join(text[start:end].split())
        if body:
            sections.append({
                "label":     label.title(),
                "css_class": known.get(label, "baseball"),
                "body":      body,
            })
    return sections


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
    print(f"  📡 generate_baseball_pulse: feed_len={len(feed_text or '')} "
          f"key_present={bool(ANTHROPIC_KEY)}")
    if not feed_text:
        print("  ⚠️  Baseball pulse: no feed text — returning empty")
        return ""
    if not ANTHROPIC_KEY:
        print("  ❌ Baseball pulse: ANTHROPIC_KEY not set — returning empty")
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

    print(f"  📡 Baseball pulse: sending prompt ({len(prompt)} chars) to {MODEL}")
    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        out = message.content[0].text.strip()
        # Claude follows the prompt and emits 'HEADER\n\nBODY\n\nHEADER\n\nBODY'.
        # The daily template's parser does split('\n\n') and expects each
        # chunk to be 'HEADER\nBODY', not alternating header/body chunks.
        # Collapse the blank line between header and its paragraph so the
        # downstream split groups header+body correctly.
        out = re.sub(
            r"^(MY TEAM|MY PROSPECTS|AROUND THE LEAGUE|BASEBALL TODAY)\n\n",
            r"\1\n",
            out,
            flags=re.MULTILINE,
        )
        print(f"  ✅ Baseball pulse: received {len(out)} chars")
        return out
    except Exception as e:
        print(f"  ❌ Baseball pulse generation error: {type(e).__name__}: {e!r}")
        return ""


def generate_farm_report(prospect_callouts: list[dict]) -> str:
    """
    Generate a narrative farm system report for WAR prospects.
    Only called when prospect_callouts has actual data.
    """
    print(f"  🌾 generate_farm_report: callouts={len(prospect_callouts or [])} "
          f"key_present={bool(ANTHROPIC_KEY)}")
    if not prospect_callouts:
        print("  ⚠️  Farm report: no callouts — returning empty")
        return ""
    if not ANTHROPIC_KEY:
        print("  ❌ Farm report: ANTHROPIC_KEY not set — returning empty")
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
        out = message.content[0].text.strip()
        print(f"  ✅ Farm report: received {len(out)} chars")
        return out
    except Exception as e:
        print(f"  ❌ Farm report generation error: {type(e).__name__}: {e!r}")
        return ""


