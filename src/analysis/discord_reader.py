"""
Reads configured Discord channels fed by TweetShift and returns
the last 24 hours of posts as a categorized text block for AI summarization.

Channel IDs:
    yard          1222998667179589682  — @MLBHRVideos
    trade-rumors  1223006407582945350  — @mlbtraderumors etc.
    mlb-official  1349393816016261151  — @MLB @MLBPipeline
    twitter-dump  1482340909952794766  — everything else

TweetShift message format:
    message["content"]                  — short preview text
    message["embeds"][0]                — rich card with full tweet data
      ["author"]["name"]                — "Account Name (@handle)"
      ["description"]                   — full tweet text
      ["image"]["url"]                  — attached image if any
      ["thumbnail"]["url"]              — thumbnail image if any

Account categories used to tag posts for the AI prompt:
    TRANSACTIONS  — roster moves, injuries, call-ups, confirmed news
    PROSPECTS     — minor league, scouting, farm system, rankings
    STATCAST      — analytics, pitch data, Statcast metrics, charts
    VIBES         — commentary, highlights, opinions, storytelling

Image whitelist — accounts whose charts are worth reading via vision:
    @pitchingninja, @baseballsavant, @fangraphs, @mlbstats,
    @pitcherlist, @tjstats, @enosarris

Noise filter — patterns that indicate promotional or context-free posts
that should be dropped before reaching the AI.

NOTE: Roster lag is 1 day in this league. Never suggest same-day adds.
"""
import os
import re
import requests
import anthropic
from datetime import datetime, timezone, timedelta

DISCORD_API   = "https://discord.com/api/v10"
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")

# Channel registry — name → ID
FEED_CHANNELS = {
    "yard":         "1222998667179589682",
    "trade-rumors": "1223006407582945350",
    "mlb-official": "1349393816016261151",
    "twitter-dump": "1482340909952794766",
}

# Account handle → category
# Keys are lowercase, no @ prefix
ACCOUNT_CATEGORIES = {
    # ── Transactions ──────────────────────────────────────────────────────────
    "mlbroostermoves":    "TRANSACTIONS",
    "mlbmovestracker":    "TRANSACTIONS",
    "mlbtransacs":        "TRANSACTIONS",
    "mlbtraderumors":     "TRANSACTIONS",
    "feinsand":           "TRANSACTIONS",
    "ken_rosenthal":      "TRANSACTIONS",
    "jeffpassan":         "TRANSACTIONS",
    "underdogmlb":        "TRANSACTIONS",
    "carloscollazo":      "TRANSACTIONS",
    "ethanh ullihen":     "TRANSACTIONS",
    # All 30 MLB team accounts
    "yankees":            "TRANSACTIONS",
    "whitesox":           "TRANSACTIONS",
    "twins":              "TRANSACTIONS",
    "tigers":             "TRANSACTIONS",
    "royals":             "TRANSACTIONS",
    "rockies":            "TRANSACTIONS",
    "reds":               "TRANSACTIONS",
    "redsox":             "TRANSACTIONS",
    "raysbaseball":       "TRANSACTIONS",
    "rangers":            "TRANSACTIONS",
    "pirates":            "TRANSACTIONS",
    "phillies":           "TRANSACTIONS",
    "padres":             "TRANSACTIONS",
    "orioles":            "TRANSACTIONS",
    "nationals":          "TRANSACTIONS",
    "mets":               "TRANSACTIONS",
    "sfgiants":           "TRANSACTIONS",
    "marlins":            "TRANSACTIONS",
    "mariners":           "TRANSACTIONS",
    "cleguardians":       "TRANSACTIONS",
    "dodgers":            "TRANSACTIONS",
    "dbacks":             "TRANSACTIONS",
    "cubs":               "TRANSACTIONS",
    "cardinals":          "TRANSACTIONS",
    "brewers":            "TRANSACTIONS",
    "braves":             "TRANSACTIONS",
    "bluejays":           "TRANSACTIONS",
    "athletics":          "TRANSACTIONS",
    "astros":             "TRANSACTIONS",
    "angels":             "TRANSACTIONS",
    # ── Prospects ─────────────────────────────────────────────────────────────
    "mlbpipeline":        "PROSPECTS",
    "milb":               "PROSPECTS",
    "baseballamerica":    "PROSPECTS",
    "kileymcd":           "PROSPECTS",
    "jimcallismlb":       "PROSPECTS",
    "joedoylemilb":       "PROSPECTS",
    "chriscleggmilb":     "PROSPECTS",
    "dynastyguru":        "PROSPECTS",
    "prospects365":       "PROSPECTS",
    "fg_prospects":       "PROSPECTS",
    "statlinescout":      "PROSPECTS",
    "ericcrossmlb":       "PROSPECTS",
    "prospectslive":      "PROSPECTS",
    "isitwelsh":          "PROSPECTS",
    "mlbprospectsbot":    "PROSPECTS",
    # ── Statcast & Analytics ──────────────────────────────────────────────────
    "mlbstats":           "STATCAST",
    "pitcherlist":        "STATCAST",
    "enosarris":          "STATCAST",
    "tjstats":            "STATCAST",
    "jonpgh":             "STATCAST",
    "pitchingninja":      "STATCAST",
    "baseballprospectus": "STATCAST",
    "tangotiger":         "STATCAST",
    "baseballsavant":     "STATCAST",
    "fangraphs":          "STATCAST",
    # ── Vibes ─────────────────────────────────────────────────────────────────
    "mlb":                "VIBES",
    "jlucroy20":          "VIBES",
    "talkinbaseball_":    "VIBES",
    "foolishbb":          "VIBES",
    "jomboymedia":        "VIBES",
    "fantasyprosmlb":     "VIBES",
    "rotoballermlb":      "VIBES",
    "rotowiremlb":        "VIBES",
    "mlbhrvideos":        "VIBES",
    "buster_espn":        "VIBES",
}

# Accounts whose chart images are worth reading via Claude vision
IMAGE_WHITELIST = {
    "pitchingninja",
    "baseballsavant",
    "fangraphs",
    "mlbstats",
    "pitcherlist",
    "tjstats",
    "enosarris",
}

# Post content patterns that indicate noise — drop these before AI sees them
# Lowercase string matching against post content
NOISE_PATTERNS = [
    # Promotions and marketing
    "sweepstakes",
    "giveaway",
    "enter to win",
    "win a",
    "click here",
    "presented by",
    "powered by",
    "sponsored by",
    "use code",
    "promo code",
    "discount",
    # Context-free retweets and meta posts
    "quoted @",
    "retweeted by",
    "subscriber chat",
    "podcast episode",
    "new episode",
    "listen now",
    "tune in",
    # Team social fluff
    "first pitch",
    "walk-up song",
    "jersey giveaway",
    "bobblehead",
    "photo of the day",
    "this week in history",
    # Ticket and merchandise
    "tickets available",
    "get tickets",
    "on sale now",
    "shop now",
    "merchandise",
]

LOOKBACK_HOURS  = 24
MIN_CONTENT_LEN = 20


def get_twitter_feed_posts() -> list[dict]:
    """
    Fetches last LOOKBACK_HOURS of TweetShift messages from all channels.
    Returns list of dicts with channel, handle, category, and content.
    Applies noise filtering and content cleaning before returning.
    """
    if not DISCORD_TOKEN:
        print("⚠️  DISCORD_BOT_TOKEN not set — skipping discord reader")
        return []

    all_posts = []
    cutoff    = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    noise_count = 0

    for channel_name, channel_id in FEED_CHANNELS.items():
        posts, channel_noise = _fetch_channel_posts(channel_id, channel_name, cutoff)
        all_posts.extend(posts)
        noise_count += channel_noise
        print(f"  📡 {channel_name}: {len(posts)} posts ({channel_noise} noise filtered)")

    print(f"  📡 Total posts: {len(all_posts)} ({noise_count} total filtered)")
    return all_posts


def get_posts_as_text() -> str:
    """
    Returns all posts grouped by category for the AI prompt.
    Each post is tagged with the source handle so the AI knows the source.
    Categories output in priority order: TRANSACTIONS → PROSPECTS → STATCAST → VIBES
    """
    posts = get_twitter_feed_posts()
    if not posts:
        return ""

    by_category: dict[str, list[str]] = {}
    for post in posts:
        cat     = post.get("category", "VIBES")
        handle  = post.get("handle", "unknown")
        content = post.get("content", "")
        by_category.setdefault(cat, []).append(f"[@{handle}] {content}")

    sections = []
    for category in ["TRANSACTIONS", "PROSPECTS", "STATCAST", "VIBES"]:
        items = by_category.get(category, [])
        if not items:
            continue
        section = f"[{category}]\n" + "\n".join(f"- {c}" for c in items)
        sections.append(section)

    return "\n\n".join(sections)


def _fetch_channel_posts(
    channel_id: str,
    channel_name: str,
    cutoff: datetime,
) -> tuple[list[dict], int]:
    """
    Fetch and parse TweetShift posts from a single Discord channel.
    Returns (posts, noise_count) tuple.
    """
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
    url     = f"{DISCORD_API}/channels/{channel_id}/messages?limit=100"

    try:
        resp = requests.get(url, headers=headers, timeout=10)

        if resp.status_code == 403:
            print(f"  ⚠️  No access to #{channel_name} — check bot permissions")
            return [], 0
        if resp.status_code == 404:
            print(f"  ⚠️  #{channel_name} not found — check channel ID")
            return [], 0
        if resp.status_code != 200:
            print(f"  ⚠️  Discord error for #{channel_name}: {resp.status_code}")
            return [], 0

        messages  = resp.json()
        posts     = []
        noise_count = 0

        for msg in messages:
            # Timestamp check
            ts_str = msg.get("timestamp", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts < cutoff:
                continue

            # Parse TweetShift embed structure
            handle, content, image_url = _parse_tweetshift_message(msg)

            # Skip empty or too-short content
            if not content or len(content) < MIN_CONTENT_LEN:
                continue

            # Skip bare URLs with no surrounding text
            if content.startswith("http") and " " not in content:
                continue

            # Skip promotional and context-free noise
            if _is_noise(content):
                noise_count += 1
                continue

            # Determine category from handle
            handle_clean = handle.lower().lstrip("@")
            category     = ACCOUNT_CATEGORIES.get(handle_clean, "VIBES")

            # Vision processing for whitelisted analytics accounts
            if handle_clean in IMAGE_WHITELIST and image_url and ANTHROPIC_KEY:
                insight = _read_image(image_url, content)
                if insight:
                    content = f"{content} [Chart insight: {insight}]"

            posts.append({
                "channel":  channel_name,
                "handle":   handle_clean,
                "category": category,
                "content":  content,
            })

        return posts, noise_count

    except Exception as e:
        print(f"  ⚠️  Discord reader error for #{channel_name}: {e}")
        return [], 0


def _is_noise(content: str) -> bool:
    """
    Returns True if the post matches known noise patterns.
    Drops promotional content, context-free retweets, and team social fluff
    before it reaches the AI.
    """
    lower = content.lower()
    return any(pattern in lower for pattern in NOISE_PATTERNS)


def _clean_content(text: str) -> str:
    """
    Clean up TweetShift message content for AI consumption.

    Strips:
    - Markdown link syntax [text](url) → text
    - Bare t.co short URLs
    - Discord escaped URLs <https://url> → plain text
    - Truncated domain references like mlbtraderumors.com/2026/...…
    - Excess whitespace and newlines
    """
    # Convert markdown links to plain text: [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # Remove Discord escaped URLs: <https://...> → plain URL
    text = re.sub(r'<(https?://[^>]+)>', r'\1', text)

    # Remove bare t.co short URLs — no value without context
    text = re.sub(r'https?://t\.co/\S+', '', text)

    # Remove truncated domain URLs like somesite.com/path/…
    text = re.sub(r'\S+\.\S+/\S*…', '', text)

    # Remove standalone full URLs that aren't meaningful content
    text = re.sub(r'https?://\S+', '', text)

    # Collapse excess whitespace and newlines
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def _parse_tweetshift_message(msg: dict) -> tuple[str, str, str | None]:
    """
    Extract handle, tweet text, and image URL from a TweetShift Discord message.

    TweetShift embed structure:
        embeds[0]["author"]["name"]    →  "Account Name (@handle)"
        embeds[0]["description"]       →  full tweet text
        embeds[0]["image"]["url"]      →  attached image
        embeds[0]["thumbnail"]["url"]  →  thumbnail image

    Falls back to message content if no embed is present.
    """
    handle    = "unknown"
    content   = msg.get("content", "").strip()
    image_url = None

    embeds = msg.get("embeds", [])
    if embeds:
        embed = embeds[0]

        # Extract @handle from author name field
        # TweetShift format: "Account Name (@handle)"
        author_name = embed.get("author", {}).get("name", "")
        if author_name:
            match = re.search(r'\(@([^)]+)\)', author_name)
            if match:
                handle = match.group(1)

        # Prefer embed description as the authoritative tweet text
        # It's almost always more complete than the message content field
        embed_desc = embed.get("description", "").strip()
        if embed_desc and len(embed_desc) > len(content):
            content = embed_desc

        # Extract image URL — check image first, then thumbnail
        image_url = (
            embed.get("image", {}).get("url")
            or embed.get("thumbnail", {}).get("url")
        )

    # Fallback: if still no handle, try to find one in the content text
    # Some TweetShift configs include the handle in the message body
    if handle == "unknown" and content:
        match = re.search(r'@([A-Za-z0-9_]+)', content)
        if match:
            handle = match.group(1)

    # Clean content for AI consumption
    content = _clean_content(content)

    return handle, content, image_url


def _read_image(image_url: str, context: str) -> str | None:
    """
    Read a chart image via Claude vision and extract the key fantasy insight.
    Only called for whitelisted analytics accounts where charts add real value.

    Returns a 1-2 sentence insight string, or None if the image is not useful.
    Cost: ~$0.001-0.002 per image at Haiku rates — negligible.
    """
    try:
        client  = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url":  image_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"This is a baseball analytics chart posted by a "
                            f"stats account. Context: {context[:200]}\n\n"
                            f"In 1-2 sentences, extract the key fantasy-relevant "
                            f"insight. Focus on: which player(s), what metric is "
                            f"shown, and whether it suggests a breakout, regression, "
                            f"or concern. "
                            f"If the image is not a meaningful baseball analytics "
                            f"chart (e.g. it is a photo, logo, video thumbnail, "
                            f"or promotional graphic), respond with exactly: SKIP"
                        ),
                    },
                ],
            }],
        )

        insight = message.content[0].text.strip()
        return None if insight == "SKIP" else insight

    except Exception as e:
        print(f"  ⚠️  Vision error: {e}")
        return None