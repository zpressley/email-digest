"""
Reads configured Discord channels fed by TweetShift and returns
the last 24 hours of posts as a flat list for AI summarization.

Channel IDs:
    yard          1222998667179589682  — @MLBHRVideos
    trade-rumors  1223006407582945350  — @mlbtraderumors etc.
    mlb-official  1349393816016261151  — @MLB @MLBPipeline
    twitter-dump  1482340909952794766  — everything else

Image whitelist — accounts whose charts/graphics are worth reading:
    @PitchingNinja   pitch movement charts
    @BaseballSavant  Statcast leaderboards
    @fangraphs       stat tables and spray charts
    @MLBStats        stat leader graphics
    @PitcherList     CSW/PLV pitch analysis charts
    @TJStats         batted ball and swing metric charts

All other image-only posts are skipped.

Vision support uses Claude's image reading capability via URL.
Cost: ~$0.001-0.002 per image at Haiku rates — negligible.

NOTE: Roster lag is 1 day in this league. Never suggest same-day adds.
All pickup urgency should be framed as "add today, active tomorrow".
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

# Accounts whose images are worth reading via vision API
# Matched against message content (TweetShift includes the handle in the post)
IMAGE_WHITELIST = {
    "@pitchingninja",
    "@baseballsavant",
    "@fangraphs",
    "@mlbstats",
    "@pitcherlist",
    "@tjstats",
}

# How far back to look
LOOKBACK_HOURS = 24

# Skip posts shorter than this — usually just a URL or empty embed
MIN_CONTENT_LENGTH = 20


def get_twitter_feed_posts() -> list[dict]:
    """
    Fetches the last LOOKBACK_HOURS of messages from all feed channels.
    Returns a list of dicts with 'channel' and 'content' keys.

    For whitelisted accounts with images, reads the image via Claude vision
    and appends the extracted insight to the post content.
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
            ts_str = msg.get("timestamp", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts < cutoff:
                continue

            content     = msg.get("content", "").strip()
            attachments = msg.get("attachments", [])
            embeds      = msg.get("embeds", [])

            # Check if this post is from a whitelisted account
            is_whitelisted = _is_whitelisted(content)

            # Skip empty posts with no attachments
            if not content and not attachments:
                continue

            # Skip bare URLs with no surrounding text (not whitelisted)
            if not is_whitelisted:
                if not content or len(content) < MIN_CONTENT_LENGTH:
                    continue
                if content.startswith("http") and " " not in content:
                    continue

            # Extract embed text (TweetShift often puts tweet text in embeds)
            embed_text = _extract_embed_text(embeds)

            # Build the full content string
            full_content = content
            if embed_text and embed_text not in content:
                full_content = f"{content} {embed_text}".strip()

            # For whitelisted accounts — try to read any image attachments
            if is_whitelisted and attachments and ANTHROPIC_KEY:
                image_insight = _read_image_attachments(
                    attachments,
                    full_content,
                    channel_name,
                )
                if image_insight:
                    full_content = (
                        f"{full_content} [Chart insight: {image_insight}]"
                    )

            # Final length check after all enrichment
            if not full_content or len(full_content) < MIN_CONTENT_LENGTH:
                continue

            posts.append({
                "channel": channel_name,
                "content": full_content,
            })

        return posts

    except Exception as e:
        print(f"  ⚠️  Discord reader error for #{channel_name}: {e}")
        return []


def _is_whitelisted(content: str) -> bool:
    """
    Returns True if the post content contains a whitelisted account handle.
    TweetShift typically includes the handle in the message text.
    """
    content_lower = content.lower()
    return any(handle in content_lower for handle in IMAGE_WHITELIST)


def _extract_embed_text(embeds: list[dict]) -> str:
    """
    Extract useful text from Discord embed objects.
    TweetShift often puts the tweet text in the embed description.
    """
    parts = []
    for embed in embeds:
        description = embed.get("description", "").strip()
        title       = embed.get("title", "").strip()
        if description and len(description) > 10:
            parts.append(description)
        elif title and len(title) > 10:
            parts.append(title)
    return " ".join(parts)


def _read_image_attachments(
    attachments: list[dict],
    post_context: str,
    channel_name: str,
) -> str | None:
    """
    For whitelisted account posts, pass image attachments to Claude vision
    and extract the fantasy-relevant insight from the chart or graphic.

    Returns a short insight string or None if nothing useful found.
    """
    if not ANTHROPIC_KEY:
        return None

    # Only process image attachments
    image_attachments = [
        a for a in attachments
        if a.get("content_type", "").startswith("image/")
        or a.get("url", "").lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
    ]

    if not image_attachments:
        return None

    # Only read the first image per post — avoid excessive token usage
    image_url = image_attachments[0].get("url")
    if not image_url:
        return None

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": image_url,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"This is a baseball analytics chart posted by "
                                f"a stats account. Context: {post_context[:200]}\n\n"
                                f"In 1-2 sentences, extract the key fantasy-relevant "
                                f"insight from this chart. Focus on: which player(s) "
                                f"are highlighted, what metric is shown, and whether "
                                f"it suggests a breakout, regression, or concern. "
                                f"If the image is not a meaningful baseball chart "
                                f"(e.g. it's a logo, photo, or video thumbnail), "
                                f"respond with exactly: SKIP"
                            ),
                        },
                    ],
                }
            ],
        )

        insight = message.content[0].text.strip()

        if insight == "SKIP" or not insight:
            return None

        return insight

    except Exception as e:
        # Vision failures are non-fatal — just skip the image
        print(f"  ⚠️  Vision read failed for {channel_name}: {e}")
        return None