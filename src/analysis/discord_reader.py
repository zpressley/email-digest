"""
Reads configured Discord channels fed by TweetShift and returns
the last 24 hours of text posts as a flat list for AI summarization.

Channel IDs:
    yard          1222998667179589682  — @MLBHRVideos
    trade-rumors  1223006407582945350  — @mlbtraderumors etc.
    mlb-official  1349393816016261151  — @MLB @MLBPipeline
    twitter-dump  1482340909952794766  — everything else

Image-only posts are skipped — text content only for now.
Vision support (reading charts/graphics) is a planned v2 feature.
"""
import os
import requests
from datetime import datetime, timezone, timedelta

DISCORD_API   = "https://discord.com/api/v10"
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Channel registry — name → ID
FEED_CHANNELS = {
    "yard":         "1222998667179589682",
    "trade-rumors": "1223006407582945350",
    "mlb-official": "1349393816016261151",
    "twitter-dump": "1482340909952794766",
}

# How far back to look
LOOKBACK_HOURS = 24

# Skip posts shorter than this — usually just a URL or empty embed
MIN_CONTENT_LENGTH = 20


def get_twitter_feed_posts() -> list[dict]:
    """
    Fetches the last LOOKBACK_HOURS of messages from all feed channels.
    Returns a list of dicts with 'channel' and 'content' keys.
    Filters out image-only posts and low-content messages.
    """
    if not DISCORD_TOKEN:
        print("⚠️  DISCORD_BOT_TOKEN not set — skipping discord reader")
        return []

    all_posts = []
    cutoff    = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    for channel_name, channel_id in FEED_CHANNELS.items():
        posts = _fetch_channel_posts(channel_id, channel_name, cutoff)
        all_posts.extend(posts)
        print(f"  📡 {channel_name}: {len(posts)} posts")

    print(f"  📡 Total discord posts: {len(all_posts)}")
    return all_posts


def get_posts_as_text() -> str:
    """
    Returns all posts as a single formatted string for the AI prompt.
    Groups by channel so Claude has source context.
    """
    posts = get_twitter_feed_posts()
    if not posts:
        return ""

    # Group by channel
    by_channel: dict[str, list[str]] = {}
    for post in posts:
        ch = post["channel"]
        by_channel.setdefault(ch, []).append(post["content"])

    sections = []
    for channel, contents in by_channel.items():
        section = f"[#{channel}]\n" + "\n".join(f"- {c}" for c in contents)
        sections.append(section)

    return "\n\n".join(sections)


def _fetch_channel_posts(
    channel_id: str,
    channel_name: str,
    cutoff: datetime,
) -> list[dict]:
    """Fetch and filter posts from a single Discord channel."""
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
    url     = f"{DISCORD_API}/channels/{channel_id}/messages?limit=100"

    try:
        resp = requests.get(url, headers=headers, timeout=10)

        if resp.status_code == 403:
            print(f"  ⚠️  No access to #{channel_name} — check bot permissions")
            return []
        if resp.status_code == 404:
            print(f"  ⚠️  Channel #{channel_name} not found — check channel ID")
            return []
        if resp.status_code != 200:
            print(f"  ⚠️  Discord API error for #{channel_name}: {resp.status_code}")
            return []

        messages = resp.json()
        posts    = []

        for msg in messages:
            # Parse timestamp
            ts_str = msg.get("timestamp", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts < cutoff:
                continue

            content = msg.get("content", "").strip()

            # Skip empty or image-only posts
            if not content or len(content) < MIN_CONTENT_LENGTH:
                continue

            # Skip posts that are just a bare URL with no surrounding text
            if content.startswith("http") and " " not in content:
                continue

            posts.append({
                "channel": channel_name,
                "content": content,
            })

        return posts

    except Exception as e:
        print(f"  ⚠️  Discord reader error for #{channel_name}: {e}")
        return []