"""
Reads the #twitter-feed Discord channel and returns
the last 24 hours of posts as a list of strings.
"""
import os
import requests
from datetime import datetime, timezone, timedelta

DISCORD_TOKEN   = os.getenv("DISCORD_BOT_TOKEN")
FEED_CHANNEL_IDS = os.getenv("TWITTER_FEED_CHANNEL_IDS", "").split(",")
DISCORD_API     = "https://discord.com/api/v10"


def get_twitter_feed_posts() -> list[str]:
    """
    Fetches the last 100 messages from #twitter-feed,
    filters to last 24 hours, returns as plain text list.
    """
    if not DISCORD_TOKEN or not TWITTER_FEED_CHANNEL_ID:
        print("⚠️  DISCORD_BOT_TOKEN or TWITTER_FEED_CHANNEL_ID not set")
        return []

    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
    url = f"{DISCORD_API}/channels/{TWITTER_FEED_CHANNEL_ID}/messages?limit=100"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"⚠️  Discord API error: {resp.status_code}")
            return []

        messages = resp.json()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        posts = []
        for msg in messages:
            # Parse Discord timestamp
            ts_str = msg.get("timestamp", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            content = msg.get("content", "").strip()
            if content:
                posts.append(content)

        return posts

    except Exception as e:
        print(f"⚠️  Discord reader error: {e}")
        return []